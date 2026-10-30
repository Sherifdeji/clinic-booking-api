"use strict";

const state = {
  slots: [],
  selectedSlot: null,
  busy: false,
  loading: false,
  retryUntil: 0,
  cooldownTimer: null,
  retryKeys: new Map(),
};
const RETRY_STORAGE = "koya.booking-retries.v1";
const REQUEST_TIMEOUT = 15000;
const elements = Object.fromEntries([
  "appointments", "booking-form", "book-another", "confirmation", "confirmation-title",
  "confirmation-id", "confirmation-name", "confirmation-provider", "confirmation-time",
  "confirmation-status", "confirmation-eyebrow", "confirmation-copy", "confirmation-mark",
  "patient-email", "email-error", "global-message", "patient-name", "name-error",
  "provider-filter", "selected-slot", "slots-empty", "slots-list", "slots-loading",
  "slots-error", "slots-error-copy", "retry-slots", "submit-booking", "slot-count",
  "timezone-note", "booking-progress", "continue-booking",
].map((id) => [id, document.getElementById(id)]));
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
elements["timezone-note"].textContent = `All times in your local time zone · ${zone.replaceAll("_", " ")}`;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showMessage(message, focus = true) {
  elements["global-message"].textContent = message;
  elements["global-message"].hidden = false;
  if (focus) elements["global-message"].focus();
}

function clearMessage() {
  elements["global-message"].hidden = true;
  elements["global-message"].textContent = "";
}

function dateLabel(value) {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  }).format(new Date(value));
}

function timeLabel(value) {
  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}

function localDay(value) {
  const date = new Date(value);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function timeRange(slot) {
  const end = localDay(slot.start) === localDay(slot.end)
    ? timeLabel(slot.end) : `${dateLabel(slot.end)}, ${timeLabel(slot.end)}`;
  return `${timeLabel(slot.start)} – ${end}`;
}

function appointmentLabel(slot) {
  return `${dateLabel(slot.start)} · ${timeRange(slot)}`;
}

function isPast(slot) {
  return new Date(slot.start).getTime() <= Date.now();
}

function canBook(slot) {
  // The API's available flag means no confirmed booking, not a future start time.
  return slot.available === true && !isPast(slot);
}

function cooldownSeconds() {
  return Math.max(0, Math.ceil((state.retryUntil - Date.now()) / 1000));
}

function syncControls() {
  const seconds = cooldownSeconds();
  elements["provider-filter"].disabled = state.busy || state.loading;
  elements["patient-name"].disabled = state.busy;
  elements["patient-email"].disabled = state.busy;
  elements["retry-slots"].disabled = state.busy || state.loading || seconds > 0;
  elements["retry-slots"].textContent = seconds > 0 ? `Try again in ${seconds}s` : "Try again";
  elements["submit-booking"].disabled = state.busy || state.loading || seconds > 0 || !state.selectedSlot;
  elements["submit-booking"].textContent = state.busy ? "Booking your visit…"
    : seconds > 0 ? `Try again in ${seconds}s` : "Confirm appointment ↗";
  elements["booking-form"].setAttribute("aria-busy", String(state.busy));
  for (const button of elements["slots-list"].querySelectorAll("button")) {
    button.disabled = state.busy || button.dataset.bookable !== "true";
  }
  elements["continue-booking"].hidden = !state.selectedSlot || state.busy;
  elements["booking-progress"].textContent = state.busy
    ? "Please wait for the booking result before leaving this page."
    : seconds > 0 ? "Please wait before trying again. The button shows the remaining time."
    : state.selectedSlot ? "Review your details, then confirm your appointment."
    : "Select a time, then confirm your appointment.";
}

function renderSelection() {
  const slot = state.selectedSlot;
  const marker = node("span", "selection-symbol", slot ? "✓" : "+");
  marker.setAttribute("aria-hidden", "true");
  const copy = node("span");
  if (slot) {
    copy.append(node("strong", "", slot.provider), node("small", "", `${appointmentLabel(slot)} · ${zone}`));
  } else {
    copy.append(document.createTextNode("Choose a time to get started."),
      node("small", "", "Your appointment details will appear here."));
  }
  elements["selected-slot"].replaceChildren(marker, copy);
  elements["selected-slot"].classList.toggle("has-selection", Boolean(slot));
  syncControls();
}

function setSelectedSlot(slot) {
  if (state.busy || !canBook(slot)) return;
  // Retry keys belong to payloads, not selection events; reselecting never discards one.
  state.selectedSlot = slot;
  clearMessage();
  renderSelection();
  renderSlots();
  if (window.matchMedia("(max-width: 700px)").matches) {
    elements["booking-form"].focus({ preventScroll: true });
    elements["selected-slot"].scrollIntoView({ block: "start" });
  } else {
    const selected = elements["slots-list"].querySelector('[aria-pressed="true"]');
    if (selected) selected.focus({ preventScroll: true });
  }
}

function renderSlots() {
  const provider = elements["provider-filter"].value;
  const slots = state.slots.filter((slot) => !provider || slot.provider === provider);
  elements["slots-list"].replaceChildren();
  elements["slots-empty"].hidden = slots.length > 0;
  const available = slots.filter(canBook).length;
  elements["slot-count"].textContent = `${available} ${available === 1 ? "time" : "times"} available`;
  const groups = new Map();
  for (const slot of slots) {
    const day = localDay(slot.start);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(slot);
  }
  let index = 0;
  for (const groupSlots of groups.values()) {
    const group = node("section", "slot-group");
    const heading = node("h3", "", dateLabel(groupSlots[0].start));
    heading.id = `slot-day-${index++}`;
    group.setAttribute("aria-labelledby", heading.id);
    const list = node("div", "slot-group-list");
    for (const slot of groupSlots) {
      const selected = state.selectedSlot?.id === slot.id;
      const bookable = canBook(slot);
      const card = node("article", `slot-card${selected ? " selected" : ""}${bookable ? "" : " unavailable"}`);
      const details = node("div", "slot-details");
      const status = !slot.available ? "Already booked" : isPast(slot) ? "Time has passed" : "Available";
      details.append(node("div", "slot-provider", slot.provider),
        node("div", "slot-time", timeRange(slot)), node("div", "slot-status", status));
      const button = node("button", "button select-button", selected ? "Selected ✓" : bookable ? "Select time" : "Unavailable");
      button.type = "button";
      button.dataset.bookable = String(bookable);
      button.setAttribute("aria-pressed", String(selected));
      button.setAttribute("aria-label", `${selected ? "Selected" : bookable ? "Select" : status}: ${slot.provider}, ${appointmentLabel(slot)}, ${zone}`);
      button.addEventListener("click", () => setSelectedSlot(slot));
      card.append(details, button);
      list.append(card);
    }
    group.append(heading, list);
    elements["slots-list"].append(group);
  }
  syncControls();
}

async function requestJSON(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, credentials: "same-origin", cache: "no-store" });
    const text = await response.text();
    let body = null;
    try { body = JSON.parse(text); } catch {
      if (response.ok) throw new Error("Invalid service response");
    }
    return { response, body };
  } finally {
    clearTimeout(timeout);
  }
}

function setCooldown(header) {
  const seconds = header && /^\d+$/.test(header.trim())
    ? Number(header) : Math.ceil((Date.parse(header || "") - Date.now()) / 1000);
  const wait = Number.isFinite(seconds) ? Math.max(1, seconds) : 60;
  state.retryUntil = Math.max(state.retryUntil, Date.now() + wait * 1000);
  clearInterval(state.cooldownTimer);
  state.cooldownTimer = setInterval(() => {
    syncControls();
    if (!cooldownSeconds()) clearInterval(state.cooldownTimer);
  }, 1000);
  syncControls();
}

async function loadSlots() {
  if (state.loading) return;
  state.loading = true;
  elements["slots-loading"].hidden = false;
  elements["slots-empty"].hidden = true;
  elements["slots-error"].hidden = true;
  elements["slots-list"].replaceChildren();
  elements["slots-list"].setAttribute("aria-busy", "true");
  elements["slot-count"].textContent = "";
  syncControls();
  try {
    const { response, body } = await requestJSON("/ui-api/slots");
    if (!response.ok) {
      if (response.status === 429) setCooldown(response.headers.get("Retry-After"));
      throw new Error("Appointments unavailable");
    }
    if (!Array.isArray(body?.slots) || !body.slots.every((slot) =>
      typeof slot.id === "string" && typeof slot.provider === "string" &&
      typeof slot.available === "boolean" && Number.isFinite(Date.parse(slot.start)) &&
      Number.isFinite(Date.parse(slot.end)) && Date.parse(slot.end) > Date.parse(slot.start))) {
      throw new Error("Invalid appointment data");
    }
    state.slots = body.slots.slice().sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
    const previous = elements["provider-filter"].value;
    const providers = [...new Set(state.slots.map((slot) => slot.provider))].sort((a, b) => a.localeCompare(b));
    elements["provider-filter"].replaceChildren(new Option("All providers", ""));
    providers.forEach((name) => elements["provider-filter"].add(new Option(name, name)));
    elements["provider-filter"].value = providers.includes(previous) ? previous : "";
    if (state.selectedSlot) {
      state.selectedSlot = state.slots.find((slot) => slot.id === state.selectedSlot.id && canBook(slot)) || null;
      renderSelection();
    }
    renderSlots();
  } catch (error) {
    state.slots = [];
    state.selectedSlot = null;
    renderSelection();
    elements["slots-error-copy"].textContent = error.name === "AbortError"
      ? "The clinic took too long to respond. Please try again."
      : "We couldn’t reach appointment availability. Please try again in a moment.";
    elements["slots-error"].hidden = false;
    elements["slots-error"].focus();
  } finally {
    state.loading = false;
    elements["slots-loading"].hidden = true;
    elements["slots-list"].setAttribute("aria-busy", "false");
    syncControls();
  }
}

function fieldError(field, message) {
  elements[`${field}-error`].textContent = message;
  elements[`patient-${field}`].setAttribute("aria-invalid", String(Boolean(message)));
}

function validateForm() {
  const nameValid = Boolean(elements["patient-name"].value.trim());
  const emailValid = Boolean(elements["patient-email"].value.trim()) && elements["patient-email"].validity.valid;
  fieldError("name", nameValid ? "" : "Please enter your full name.");
  fieldError("email", emailValid ? "" : "Please enter a valid email address.");
  if (!nameValid || !emailValid) {
    elements[nameValid ? "patient-email" : "patient-name"].focus();
    return false;
  }
  if (!state.selectedSlot || !canBook(state.selectedSlot)) {
    state.selectedSlot = null;
    renderSelection();
    renderSlots();
    showMessage("Please select an available future appointment.");
    return false;
  }
  return true;
}

async function getIdempotencyKey(payload) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("Retry protection needs a secure browser connection. Please open this page using HTTPS (or localhost).");
  }
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  // Only opaque keys and SHA-256 fingerprints are persisted; never contact details or payloads.
  try {
    const stored = JSON.parse(sessionStorage.getItem(RETRY_STORAGE) || "{}");
    for (const [hash, key] of Object.entries(stored)) {
      if (/^[a-f0-9]{64}$/.test(hash) && typeof key === "string" && /^[a-f0-9-]{32,36}$/i.test(key)) {
        state.retryKeys.set(hash, key);
      }
    }
    if (!state.retryKeys.has(fingerprint)) {
      const key = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
      state.retryKeys.set(fingerprint, key);
    }
    // Persist before sending so a refresh can safely retry the same payload.
    sessionStorage.setItem(RETRY_STORAGE, JSON.stringify(Object.fromEntries(state.retryKeys)));
  } catch {
    throw new Error("Your browser couldn’t save booking retry protection. Please allow session storage for this site, then try again. No booking request was sent.");
  }
  return { key: state.retryKeys.get(fingerprint), fingerprint };
}

function clearRetryKey(fingerprint) {
  state.retryKeys.delete(fingerprint);
  try {
    if (state.retryKeys.size) sessionStorage.setItem(RETRY_STORAGE, JSON.stringify(Object.fromEntries(state.retryKeys)));
    else sessionStorage.removeItem(RETRY_STORAGE);
  } catch { /* A retained key is safe to replay if storage becomes unavailable. */ }
}

function showConfirmation(booking, slot) {
  const confirmed = booking.status === "confirmed";
  elements["confirmation-name"].textContent = booking.name;
  elements["confirmation-id"].textContent = booking.id;
  elements["confirmation-status"].textContent = booking.status;
  elements["confirmation-provider"].textContent = slot.provider;
  elements["confirmation-time"].textContent = `${appointmentLabel(slot)} · ${zone}`;
  elements["confirmation-eyebrow"].textContent = confirmed ? "Appointment confirmed" : "Booking received";
  elements["confirmation-mark"].textContent = confirmed ? "✓" : "i";
  elements["confirmation-copy"].textContent = confirmed
    ? "Your appointment is confirmed. Keep this booking reference for your records."
    : `The clinic returned the status “${booking.status}”. This is not a confirmed appointment. Keep your reference for your records.`;
  elements.appointments.hidden = true;
  elements.confirmation.hidden = false;
  clearMessage();
  elements["confirmation-title"].focus();
}

async function submitBooking(event) {
  event.preventDefault();
  // This guard must run before validation, digesting, or any other asynchronous work.
  if (state.busy || state.loading || cooldownSeconds()) return;
  clearMessage();
  if (!validateForm()) return;
  const slot = { ...state.selectedSlot };
  const payload = {
    slot_id: slot.id,
    name: elements["patient-name"].value.trim(),
    email: elements["patient-email"].value.trim().toLowerCase(),
  };
  state.busy = true;
  syncControls();
  let sent = false;
  try {
    const retry = await getIdempotencyKey(payload);
    sent = true;
    const { response, body } = await requestJSON("/ui-api/bookings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": retry.key },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      if (response.status === 404 || response.status === 409) {
        state.selectedSlot = null;
        renderSelection();
        showMessage(response.status === 409
          ? "That time is no longer available for this booking. Please choose another appointment."
          : "That appointment could not be found. Please choose another time.");
        await loadSlots();
      } else if (response.status === 429) {
        setCooldown(response.headers.get("Retry-After"));
        showMessage("Too many booking attempts. Please wait for the countdown, then try again. Your details and retry protection have been kept.");
      } else if (response.status === 400 || response.status === 422) {
        showMessage(typeof body?.error === "string"
          ? `${body.error}. Please check your details and try again.`
          : "The clinic couldn’t accept these details. Please check your name and email address and try again.");
      } else {
        showMessage("We couldn’t verify whether your booking completed. Retry with the same time and details to safely check the result; your retry key has been kept.");
      }
      return;
    }
    if (!body || typeof body.id !== "string" || typeof body.name !== "string" ||
        typeof body.status !== "string" || !body.status || body.slot_id !== slot.id) {
      throw new Error("Invalid booking response");
    }
    clearRetryKey(retry.fingerprint);
    showConfirmation(body, slot);
    state.selectedSlot = null;
    elements["booking-form"].reset();
  } catch (error) {
    showMessage(!sent ? error.message : error.name === "AbortError"
      ? "The request timed out, so we can’t tell whether your booking completed. Retry with the same time and details to safely check the result. Your retry key has been kept."
      : "The connection was interrupted or the clinic returned an unreadable result. Your booking may have completed. Retry with the same time and details; your retry key has been kept.");
  } finally {
    state.busy = false;
    syncControls();
  }
}

function resetForm() {
  if (state.busy) return;
  state.selectedSlot = null;
  elements.confirmation.hidden = true;
  elements.appointments.hidden = false;
  elements["booking-form"].reset();
  fieldError("name", "");
  fieldError("email", "");
  clearMessage();
  renderSelection();
  elements["provider-filter"].value = "";
  elements["provider-filter"].focus();
  loadSlots();
}

elements["provider-filter"].addEventListener("change", () => {
  if (!state.busy) renderSlots();
});
elements["retry-slots"].addEventListener("click", async () => {
  if (state.busy || state.loading || cooldownSeconds()) return;
  await loadSlots();
  if (elements["slots-error"].hidden) elements["provider-filter"].focus();
});
elements["booking-form"].addEventListener("submit", submitBooking);
elements["book-another"].addEventListener("click", resetForm);
elements["continue-booking"].addEventListener("click", (event) => {
  event.preventDefault();
  elements["patient-name"].focus();
});
for (const field of ["name", "email"]) {
  elements[`patient-${field}`].addEventListener("input", () => fieldError(field, ""));
}

// Keep a long-open page from offering a time whose start has passed, without
// rerendering the list (and losing keyboard focus) on every timer tick.
setInterval(() => {
  if (state.busy || state.loading || elements.appointments.hidden) return;
  const buttons = [...elements["slots-list"].querySelectorAll("button")];
  const provider = elements["provider-filter"].value;
  const visible = state.slots.filter((slot) => !provider || slot.provider === provider);
  const changed = visible.some((slot, index) => buttons[index]?.dataset.bookable !== String(canBook(slot)));
  if (state.selectedSlot && !canBook(state.selectedSlot)) {
    state.selectedSlot = null;
    renderSelection();
    showMessage("That appointment’s start time has passed. Please choose another time.", false);
  }
  if (changed) renderSlots();
}, 15000);

loadSlots();
