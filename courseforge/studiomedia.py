"""Watch a Canvas Studio recording here instead of in SpeedGrader.

A Studio recording never reaches the Studio as a file: the submission holds a
launch link into Canvas Studio, and Studio only plays it to someone Canvas has
signed in. Canvas will sign a launch for us. The sessionless launch endpoint
turns the API token into a one-time link that opens Studio's own player as the
instructor. It opens in a window of its own beside the Studio: Canvas refuses
to load inside another site's page, so a frame in the work pane stays empty.
Nothing is downloaded, and the link is asked for fresh on every click because
Canvas lets it be used once.

Read only. The launch is a view, the same one SpeedGrader opens.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .extract import STUDIO_HOST, linked_media_part


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_studio(url: str) -> bool:
    host = _host(url)
    return urlparse(url).scheme == "https" and (
        host == STUDIO_HOST or host.endswith("." + STUDIO_HOST))


def tool_url(sub: dict | None) -> str:
    """The Studio launch link a submission carries, or "" if it has none."""
    part = linked_media_part(sub)
    if not part or not part["data"]["video"]:
        return ""
    for key in ("external_tool_url", "url"):
        url = str((sub or {}).get(key) or "")
        if is_studio(url):
            return url
    return ""


def launch(client, store, course_id, assignment_id, user_id) -> dict:
    """A one-time link that opens this student's recording in Studio's player."""
    uid = str(user_id or "")
    if not uid.isdigit():
        raise ValueError("no student given")
    adir = store.assignment_dir(course_id, assignment_id)
    subs = store.read(adir / "submissions.json", []) or []
    sub = next((s for s in subs if isinstance(s, dict)
                and str(s.get("user_id")) == uid), None)
    url = tool_url(sub)
    if not url:
        raise ValueError("this student has no Canvas Studio recording on this assignment")
    got = client.get(f"/courses/{course_id}/external_tools/sessionless_launch", url=url)
    link = str((got or {}).get("url") or "")
    # The link is opened in the browser, not by us, but it still has to be the
    # Canvas this Studio talks to before the page sends anyone to it.
    if urlparse(link).scheme != "https" or _host(link) != _host(client.base):
        raise RuntimeError("Canvas did not return a launch link for the recording")
    return {"url": link}
