"""Reusable login-form wait and fill helpers for desktop browser workers.

The helpers only handle the login form. Captcha or access checks remain manual.
No credentials are logged by this module.
"""

from __future__ import annotations

import asyncio
from typing import Any

from bet_desktop.models.state_temporal_guard import now_ms


LOGIN_PREPARE_JS = r"""
async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 8
      && rect.height > 8
      && style.display !== "none"
      && style.visibility !== "hidden"
      && !el.disabled;
  };
  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
  const visibleInputs = () => Array.from(document.querySelectorAll("input, textarea"))
    .filter((el) => {
      if (!isVisible(el)) return false;
      const type = String(el.type || "").toLowerCase();
      return !["hidden", "checkbox", "radio", "button", "submit", "reset"].includes(type);
    })
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return ar.top === br.top ? ar.left - br.left : ar.top - br.top;
    });

  const inputsBefore = visibleInputs();
  const firstInputTop = inputsBefore.length ? inputsBefore[0].getBoundingClientRect().top : Infinity;
  const loginRe = /(\u767b\u5f55|\u767b\u5165|login|sign\s*in)/i;
  const registerRe = /(\u6ce8\u518c|register|sign\s*up)/i;
  const candidates = Array.from(document.querySelectorAll("button, a, [role=button], [role=tab], div, span, li"))
    .filter((el) => {
      if (!isVisible(el)) return false;
      const text = textOf(el);
      if (!loginRe.test(text)) return false;
      if (registerRe.test(text) && text.length > 8) return false;
      const rect = el.getBoundingClientRect();
      return rect.top < firstInputTop - 2;
    })
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      const aExact = /^(\u767b\u5f55|\u767b\u5165|login)$/i.test(textOf(a).replace(/\s+/g, "")) ? 0 : 1;
      const bExact = /^(\u767b\u5f55|\u767b\u5165|login)$/i.test(textOf(b).replace(/\s+/g, "")) ? 0 : 1;
      if (aExact !== bExact) return aExact - bExact;
      return Math.abs(ar.top - firstInputTop) - Math.abs(br.top - firstInputTop);
    });
  if (candidates.length) {
    const clickTarget = candidates[0].closest("[role=tab], button, a, li") || candidates[0];
    clickTarget.click();
    await sleep(350);
  }

  for (const input of document.querySelectorAll("[data-codex-login-user], [data-codex-login-password]")) {
    input.removeAttribute("data-codex-login-user");
    input.removeAttribute("data-codex-login-password");
  }
  const bodyText = textOf(document.body);
  const inputs = visibleInputs();
  const registerHints = [
    "\u786e\u8ba4\u5bc6\u7801",
    "\u518d\u6b21\u8f93\u5165",
    "\u771f\u5b9e\u59d3\u540d",
    "\u7528\u6237\u534f\u8bae"
  ].filter((word) => bodyText.includes(word)).length;
  if (registerHints >= 2 || inputs.length >= 4) {
    return { ok: false, activeKind: "register", inputCount: inputs.length, registerHints };
  }
  const passwordInputs = inputs.filter((input) => {
    const haystack = [
      input.type,
      input.name,
      input.id,
      input.placeholder,
      input.autocomplete
    ].join(" ").toLowerCase();
    return haystack.includes("password")
      || haystack.includes("pass")
      || haystack.includes("\u5bc6\u7801")
      || haystack.includes("\u5bc6\u78bc");
  });
  if (!passwordInputs.length || inputs.length < 2) {
    return { ok: false, activeKind: "unknown", inputCount: inputs.length, passwordCount: passwordInputs.length, registerHints };
  }
  const passwordInput = passwordInputs[0];
  const passwordTop = passwordInput.getBoundingClientRect().top;
  const userCandidates = inputs.filter((input) => input !== passwordInput && input.getBoundingClientRect().top <= passwordTop + 8);
  const userInput = userCandidates.length ? userCandidates[userCandidates.length - 1] : inputs.find((input) => input !== passwordInput);
  if (!userInput) {
    return { ok: false, activeKind: "unknown", inputCount: inputs.length, passwordCount: passwordInputs.length, registerHints };
  }
  userInput.setAttribute("data-codex-login-user", "1");
  passwordInput.setAttribute("data-codex-login-password", "1");
  return { ok: true, activeKind: "login", inputCount: inputs.length, passwordCount: passwordInputs.length, registerHints };
}
"""


ACTIVATE_LOGIN_TAB_JS = r"""
async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 8
      && rect.height > 8
      && style.display !== "none"
      && style.visibility !== "hidden"
      && !el.disabled;
  };
  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
  const inputs = Array.from(document.querySelectorAll("input, textarea")).filter(isVisible);
  const firstInputTop = inputs.length ? Math.min(...inputs.map((el) => el.getBoundingClientRect().top)) : Infinity;
  const loginRe = /(^|\s)(\u767b\u5f55|\u767b\u5165|login|sign\s*in)(\s|$)|(\u4f1a\u5458\u767b\u5f55|\u8d26\u53f7\u767b\u5f55|\u5e10\u53f7\u767b\u5f55)/i;
  const registerRe = /(\u6ce8\u518c|register|sign\s*up)/i;
  const nodes = Array.from(document.querySelectorAll("button, a, [role=button], [role=tab], label, div, span, li"));
  const candidates = nodes
    .filter((el) => {
      if (!isVisible(el)) return false;
      const text = textOf(el);
      if (!text || text.length > 24) return false;
      if (!loginRe.test(text)) return false;
      if (registerRe.test(text)) return false;
      const rect = el.getBoundingClientRect();
      return rect.top <= firstInputTop + 80;
    })
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const text = textOf(el);
      const exact = /^(\u767b\u5f55|\u767b\u5165|login)$/i.test(text.replace(/\s+/g, "")) ? 0 : 1;
      const nearInput = Math.abs(rect.top - firstInputTop);
      return { el, rect, text, score: exact * 1000 + nearInput + rect.width * rect.height / 100000 };
    })
    .sort((a, b) => a.score - b.score);
  if (candidates.length) {
    const el = candidates[0].el;
    const clickTarget = el.closest("[role=tab], button, a, li, label") || el;
    clickTarget.click();
    await sleep(500);
    return { ok: true, method: "text", text: candidates[0].text };
  }
  return { ok: false, method: "none" };
}
"""


CLICK_LOGIN_TAB_BY_FORM_GEOMETRY_JS = r"""
async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 8
      && rect.height > 8
      && style.display !== "none"
      && style.visibility !== "hidden"
      && !el.disabled;
  };
  const inputs = Array.from(document.querySelectorAll("input, textarea"))
    .filter((el) => {
      if (!isVisible(el)) return false;
      const type = String(el.type || "").toLowerCase();
      return !["hidden", "checkbox", "radio", "button", "submit", "reset"].includes(type);
    })
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return ar.top === br.top ? ar.left - br.left : ar.top - br.top;
    });
  if (!inputs.length) return { ok: false, reason: "no_inputs" };
  const rect = inputs[0].getBoundingClientRect();
  const points = [
    { x: rect.left + rect.width * 0.75, y: rect.top - 54 },
    { x: rect.left + rect.width * 0.75, y: rect.top - 42 },
    { x: rect.left + rect.width * 0.70, y: rect.top - 66 }
  ];
  for (const point of points) {
    const el = document.elementFromPoint(point.x, point.y);
    if (!el) continue;
    const clickTarget = el.closest("[role=tab], button, a, li, label, div, span") || el;
    clickTarget.click();
    await sleep(450);
    return {
      ok: true,
      method: "form_geometry",
      x: Math.round(point.x),
      y: Math.round(point.y),
      tag: clickTarget.tagName || ""
    };
  }
  return { ok: false, reason: "no_click_target" };
}
"""


async def wait_for_login_form(context: Any, page: Any, *, timeout_seconds: int = 120) -> tuple[Any, Any]:
    """Wait until an account/password login form appears in any current page/frame."""

    deadline = now_ms() + max(10, timeout_seconds) * 1000
    last_active_click_ms = 0
    while now_ms() < deadline:
        target = await _find_login_target_in_context(context, preferred_page=page)
        if target is not None:
            return target
        if now_ms() - last_active_click_ms >= 2500:
            last_active_click_ms = now_ms()
            await _try_open_login_panel(page)
        await asyncio.sleep(0.5)
    raise TimeoutError("login form did not appear before timeout")


async def fill_login_form_when_visible(
    context: Any,
    page: Any,
    *,
    username: str,
    password: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Wait for a login form, fill credentials, and submit the form."""

    if not username or not password:
        return {"ok": False, "stage": "missing_credentials"}

    login_page, target = await wait_for_login_form(context, page, timeout_seconds=timeout_seconds)
    prepared: dict[str, Any] = {}
    for attempt in range(4):
        prepared = await _prepare_inputs(target)
        if prepared.get("ok"):
            break
        await _activate_login_tab(login_page, target, attempt=attempt)
        await asyncio.sleep(0.4)
        found = await _find_login_target_in_context(context, preferred_page=login_page)
        if found is not None:
            login_page, target = found
    if not prepared.get("ok"):
        return {"ok": False, "stage": "prepare_failed", "details": _safe_details(prepared)}

    user_locator = target.locator('[data-codex-login-user="1"]').first
    password_locator = target.locator('[data-codex-login-password="1"]').first
    await user_locator.wait_for(state="visible", timeout=5000)
    await password_locator.wait_for(state="visible", timeout=5000)
    await user_locator.fill(username, timeout=5000)
    await password_locator.fill(password, timeout=5000)

    clicked = await _click_submit_near_password(target, password_locator)
    if not clicked:
        try:
            await password_locator.press("Enter", timeout=1500)
            clicked = True
        except Exception:
            clicked = False
    await login_page.wait_for_timeout(1000)
    return {"ok": True, "stage": "submitted", "clicked_submit": clicked}


async def _find_login_target_in_context(context: Any, *, preferred_page: Any | None = None) -> tuple[Any, Any] | None:
    pages: list[Any] = []
    if preferred_page is not None:
        pages.append(preferred_page)
    try:
        all_pages = list(context.pages)
    except Exception:
        all_pages = []
    for candidate in all_pages:
        if candidate not in pages:
            pages.append(candidate)

    for candidate in pages:
        try:
            frames = list(candidate.frames)
        except Exception:
            continue
        for frame in frames:
            if await _frame_has_login_inputs(frame):
                return candidate, frame
    return None


async def _frame_has_login_inputs(frame: Any) -> bool:
    try:
        password_count = await frame.locator("input[type=password]:visible").count()
        input_count = await frame.locator("input:visible").count()
        if password_count >= 1 and input_count >= 2:
            return True
    except Exception:
        pass
    try:
        counts = await frame.evaluate(
            r"""
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 8 && rect.height > 8
                  && style.display !== "none"
                  && style.visibility !== "hidden"
                  && !el.disabled;
              };
              const inputs = Array.from(document.querySelectorAll("input, textarea")).filter(visible);
              const passwordInputs = inputs.filter((input) => {
                const haystack = [input.type, input.name, input.id, input.placeholder, input.autocomplete]
                  .join(" ").toLowerCase();
                return haystack.includes("password")
                  || haystack.includes("pass")
                  || haystack.includes("\u5bc6\u7801")
                  || haystack.includes("\u5bc6\u78bc");
              });
              return { inputCount: inputs.length, passwordCount: passwordInputs.length };
            }
            """
        )
    except Exception:
        return False
    return isinstance(counts, dict) and int(counts.get("passwordCount") or 0) >= 1 and int(counts.get("inputCount") or 0) >= 2


async def _try_open_login_panel(page: Any) -> None:
    selectors = (
        "text=Login",
        "button:has-text('Login')",
        "[role=button]:has-text('Login')",
        "a:has-text('Login')",
        "text=\u767b\u5f55",
        "text=\u767b\u5165",
        "button:has-text('\u767b\u5f55')",
        "[role=button]:has-text('\u767b\u5f55')",
        "a:has-text('\u767b\u5f55')",
    )
    for selector in selectors:
        try:
            await page.locator(selector).first.click(timeout=600)
            await page.wait_for_timeout(350)
            return
        except Exception:
            continue

    try:
        viewport = page.viewport_size or {"width": 1360, "height": 820}
        width = float(viewport.get("width", 1360))
        height = float(viewport.get("height", 820))
        for x, y in (
            (width - 180, 48),
            (width - 110, 48),
            (160, 48),
            (180, 72),
            (width / 2 + 110, 28),
            (width / 2, height - 70),
        ):
            await page.mouse.click(x, y)
            await page.wait_for_timeout(250)
    except Exception:
        return


async def _prepare_inputs(target: Any) -> dict[str, Any]:
    try:
        result = await target.evaluate(LOGIN_PREPARE_JS)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid_prepare_result"}


async def _activate_login_tab(page: Any, target: Any, *, attempt: int) -> dict[str, Any]:
    try:
        result = await target.evaluate(ACTIVATE_LOGIN_TAB_JS)
    except Exception as exc:
        result = {"ok": False, "error": type(exc).__name__}
    if isinstance(result, dict) and result.get("ok"):
        return result

    try:
        geometry_result = await target.evaluate(CLICK_LOGIN_TAB_BY_FORM_GEOMETRY_JS)
    except Exception as exc:
        geometry_result = {"ok": False, "error": type(exc).__name__}
    if isinstance(geometry_result, dict) and geometry_result.get("ok"):
        return geometry_result

    # 228.com often opens on the register tab. This fallback mirrors the tested
    # probe click that switches the modal back to the login tab without touching
    # any game/betting area.
    try:
        viewport = page.viewport_size or {"width": 1360, "height": 820}
        width = float(viewport.get("width", 1360))
        fallback_points = (
            (width / 2 + 95, 266),
            (width / 2 - 95, 266),
            (width / 2, 225),
        )
        x, y = fallback_points[min(attempt, len(fallback_points) - 1)]
        await page.mouse.click(x, y)
        await page.wait_for_timeout(500)
        return {"ok": True, "method": "coordinate_fallback", "attempt": attempt}
    except Exception as exc:
        return {"ok": False, "method": "coordinate_fallback", "error": type(exc).__name__}


async def _click_submit_near_password(target: Any, password_locator: Any) -> bool:
    try:
        password_box = await password_locator.bounding_box(timeout=3000)
    except Exception:
        password_box = None
    candidates = target.locator(
        "button:has-text('Login'), [role=button]:has-text('Login'), a:has-text('Login'), "
        "button:has-text('\u767b\u5f55'), button:has-text('\u767b\u5165'), "
        "[role=button]:has-text('\u767b\u5f55'), [role=button]:has-text('\u767b\u5165'), "
        "a:has-text('\u767b\u5f55'), a:has-text('\u767b\u5165'), "
        "div:has-text('\u767b\u5f55'), div:has-text('\u767b\u5165')"
    )
    if password_box is None:
        return False
    password_bottom = password_box["y"] + password_box["height"]
    count = min(await candidates.count(), 40)
    for index in range(count):
        candidate = candidates.nth(index)
        try:
            box = await candidate.bounding_box(timeout=500)
        except Exception:
            box = None
        if box is None:
            continue
        if box["y"] >= password_bottom - 4 and 50 <= box["width"] <= 420 and 20 <= box["height"] <= 90:
            try:
                await candidate.click(timeout=1500)
                return True
            except Exception:
                continue
    return False


def _safe_details(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"username", "password"}}

