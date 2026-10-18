"""Unit tests for reading and releasing files_lock locks.

``get_lock`` must tell the Text editor's session lock (an app lock owned by
``text``) apart from a lock a person set, because only the former may be
released on a writer's behalf. ``release_lock`` must report a refusal (423)
instead of raising, and treat "no lock" (412) as unlocked.
"""

from typing import Any

import httpx
import pytest

from nextcloud_mcp_server.client.webdav import WebDAVClient

pytestmark = pytest.mark.unit

TEXT_LOCK = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns" xmlns:oc="http://owncloud.org/ns">
 <d:response><d:href>/remote.php/dav/files/alice/.Collectives/Wiki/Page.md</d:href>
  <d:propstat><d:prop>
   <nc:lock>1</nc:lock><nc:lock-owner-type>1</nc:lock-owner-type>
   <nc:lock-owner-editor>text</nc:lock-owner-editor><oc:fileid>259</oc:fileid>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>"""

UNLOCKED = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns" xmlns:oc="http://owncloud.org/ns">
 <d:response><d:href>/remote.php/dav/files/alice/Page.md</d:href>
  <d:propstat><d:prop><oc:fileid>259</oc:fileid></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  <d:propstat><d:prop><nc:lock/><nc:lock-owner-type/><nc:lock-owner-editor/></d:prop>
   <d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
 </d:response>
</d:multistatus>"""


def _client(mocker) -> Any:
    client: Any = WebDAVClient(mocker.AsyncMock(spec=httpx.AsyncClient), "alice")
    client._ensure_principal_id = mocker.AsyncMock()
    return client


def _response(status: int, body: bytes = b"") -> httpx.Response:
    return httpx.Response(status, content=body, request=httpx.Request("X", "http://nc"))


def _status_error(status: int) -> httpx.HTTPStatusError:
    response = _response(status)
    return httpx.HTTPStatusError(
        str(status), request=response.request, response=response
    )


async def test_text_editor_lock_is_reported_with_its_owner(mocker):
    client = _client(mocker)
    client._make_request = mocker.AsyncMock(return_value=_response(207, TEXT_LOCK))

    lock = await client.get_lock(".Collectives/Wiki/Page.md")

    assert lock == {"file_id": 259, "owner_type": 1, "editor": "text"}
    method, _ = client._make_request.call_args.args
    assert method == "PROPFIND"


async def test_malformed_fileid_is_not_fatal(mocker):
    client = _client(mocker)
    body = TEXT_LOCK.replace(b"<oc:fileid>259</oc:fileid>", b"<oc:fileid>x</oc:fileid>")
    client._make_request = mocker.AsyncMock(return_value=_response(207, body))

    lock = await client.get_lock("Page.md")

    assert lock == {"file_id": None, "owner_type": 1, "editor": "text"}


async def test_unlocked_file_has_no_lock(mocker):
    client = _client(mocker)
    client._make_request = mocker.AsyncMock(return_value=_response(207, UNLOCKED))

    assert await client.get_lock("Page.md") is None


async def test_release_lock_calls_the_files_lock_api(mocker):
    client = _client(mocker)
    client._make_request = mocker.AsyncMock(return_value=_response(200))

    assert await client.release_lock(259) is True
    method, url = client._make_request.call_args.args
    assert (method, url) == ("DELETE", "/ocs/v2.php/apps/files_lock/lock/259")


@pytest.mark.parametrize(
    ("status", "released"), [(412, True), (423, False)], ids=["no-lock", "refused"]
)
async def test_release_lock_reports_known_outcomes(mocker, status, released):
    client = _client(mocker)
    client._make_request = mocker.AsyncMock(side_effect=_status_error(status))

    assert await client.release_lock(259) is released


async def test_release_lock_raises_other_errors(mocker):
    client = _client(mocker)
    client._make_request = mocker.AsyncMock(side_effect=_status_error(500))
    release = client.release_lock(259)

    with pytest.raises(httpx.HTTPStatusError):
        await release
