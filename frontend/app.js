const state = {
  slots: [],
  selectedSlot: null,
  idempotencyKey: null,
  requestFingerprint: null,
};

const elements = {
  bookingForm: document.querySelector("#booking-form"),
  bookAnother: document.querySelector("#book-another"),
  confirmation: document.querySelector("#confirmation"),
  confirmationId: document.querySelector("#confirmation-id"),
  confirmationName: document.querySelector("#confirmation-name"),
  confirmationProvider: document.querySelector("#confirmation-provider"),
  confirmationTime: document.querySelector("#confirmation-time"),
  email: document.querySelector("#patient-email"),
  emailError: document.querySelector("#email-error"),
  globalMessage: document.querySelector("#global-message"),
  menuToggle: document.querySelector("#menu-toggle"),
  name: document.querySelector("#patient-name"),
  nameError: document.querySelector("#name-error"),
  nav: document.querySelector("#primary-nav"),
  providerFilter: document.querySelector("#provider-filter"),
  selectedSlot: document.querySelector("#selected-slot"),
  slotsEmpty: document.querySelector("#slots-empty"),
  slotsList: document.querySelector("#slots-list"),
  slotsLoading: document.querySelector("#slots-loading"),
  submitBooking: document.querySelector("#submit-booking"),
};

function showMessage(message) {
  elements.globalMessage.textContent = message;
  elements.globalMessage.hidden = false;
}

function clearMessage() {
  elements.globalMessage.hidden = true;
  elements.globalMessage.textContent = "";
}

function dateParts(value) {
  const date = new Date(value);
  return {
    day: new Intl.DateTimeFormat(undefined, { day: "2-digit" }).format(date),
    month: new Intl.DateTimeFormat(undefined, { month: "short" }).format(date),
    weekday: new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date),
  };
}

function formatTime(value) {
  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}

function formatDateTime(start, end) {
  const date = new Intl.DateTimeFormat(undefined, { dateStyle: "full" }).format(new Date(start));
  return `${date} · ${formatTime(start)}–${formatTime(end)}`;
}

function setSelectedSlot(slot) {
  state.selectedSlot = slot;
  state.idempotencyKey = null;
  state.requestFingerprint = null;
  elements.selectedSlot.replaceChildren();

  const date = dateParts(slot.start);
  const dateText = document.createElement("strong");
  dateText.textContent = `${date.weekday}, ${date.month} ${date.day}`;
  const timeText = document.createElement("small");
  timeText.textContent = `${formatTime(slot.start)}–${formatTime(slot.end)} · ${slot.provider}`;
  const copy = document.createElement("span");
  copy.append(dateText, timeText);
  const marker = document.createElement("span");
  marker.className = "selected-placeholder-icon";
  marker.setAttribute("aria-hidden", "true");
  marker.textContent = "✓";
  elements.selectedSlot.append(marker, copy);
  elements.submitBooking.disabled = false;
  elements.selectedSlot.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderSlots() {
  elements.slotsList.replaceChildren();
  elements.slotsEmpty.hidden = state.slots.length > 0;

  for (const slot of state.slots) {
    const date = dateParts(slot.start);
    const card = document.createElement("article");
    card.className = `slot-card${slot.available ? "" : " unavailable"}`;

    const dateElement = document.createElement("div");
    dateElement.className = "slot-date";
    dateElement.innerHTML = `<strong>${date.day}</strong><span>${date.month}</span><span>${date.weekday}</span>`;

    const details = document.createElement("div");
    const provider = document.createElement("div");
    provider.className = "slot-provider";
    provider.textContent = slot.provider;
    const time = document.createElement("div");
    time.className = "slot-time";
    time.textContent = `${formatTime(slot.start)} – ${formatTime(slot.end)}`;
    const availability = document.createElement("div");
    availability.className = "slot-status";
    availability.textContent = slot.available ? "Available to book" : "Already booked";
    details.append(provider, time, availability);

    const button = document.createElement("button");
    button.className = "button select-button";
    button.type = "button";
    button.textContent = slot.available ? "Select time" : "Unavailable";
    button.disabled = !slot.available;
    button.addEventListener("click", () => setSelectedSlot(slot));
    card.append(dateElement, details, button);
    elements.slotsList.append(card);
  }
}

async function loadSlots() {
  elements.slotsLoading.hidden = false;
  elements.slotsEmpty.hidden = true;
  elements.slotsList.replaceChildren();
  try {
    const provider = elements.providerFilter.value;
    const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    const response = await fetch(`/ui-api/slots${query}`);
    const body = await response.json();
    if (!response.ok) throw new Error("Unable to load appointments.");

    state.slots = body.slots;
    if (!provider) {
      const providers = [...new Set(state.slots.map((slot) => slot.provider))].sort();
      elements.providerFilter.replaceChildren(new Option("All providers", ""));
      providers.forEach((name) => elements.providerFilter.add(new Option(name, name)));
    }
    renderSlots();
  } catch {
    showMessage("We could not load appointments right now. Please refresh and try again.");
  } finally {
    elements.slotsLoading.hidden = true;
  }
}

function validateForm() {
  const name = elements.name.value.trim();
  const email = elements.email.value.trim();
  elements.nameError.textContent = name ? "" : "Please enter your full name.";
  elements.emailError.textContent = email && elements.email.validity.valid
    ? "" : "Please enter a valid email address.";
  return Boolean(name && email && elements.email.validity.valid && state.selectedSlot);
}

function getIdempotencyKey(payload) {
  const fingerprint = JSON.stringify(payload);
  if (!state.idempotencyKey || state.requestFingerprint !== fingerprint) {
    state.idempotencyKey = crypto.randomUUID();
    state.requestFingerprint = fingerprint;
  }
  return state.idempotencyKey;
}

function showConfirmation(booking) {
  const slot = state.selectedSlot;
  elements.confirmationId.textContent = booking.id;
  elements.confirmationName.textContent = booking.name;
  elements.confirmationProvider.textContent = slot?.provider || "Your provider";
  elements.confirmationTime.textContent = slot ? formatDateTime(slot.start, slot.end) : booking.slot_id;
  elements.confirmation.hidden = false;
  document.querySelector("#appointments").hidden = true;
  elements.confirmation.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitBooking(event) {
  event.preventDefault();
  clearMessage();
  if (!validateForm()) {
    showMessage("Select an available time and complete the required details.");
    return;
  }

  const payload = {
    slot_id: state.selectedSlot.id,
    name: elements.name.value.trim(),
    email: elements.email.value.trim(),
  };
  const selectedAtSubmit = state.selectedSlot;
  elements.submitBooking.disabled = true;
  elements.submitBooking.textContent = "Confirming…";

  try {
    const response = await fetch("/ui-api/bookings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": getIdempotencyKey(payload),
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 409) {
        showMessage("That time was just booked. Please choose another appointment.");
        state.selectedSlot = null;
        state.idempotencyKey = null;
        state.requestFingerprint = null;
        elements.selectedSlot.textContent = "Select an available time and it will appear here.";
        await loadSlots();
      } else if (response.status === 429) {
        showMessage(`Too many booking attempts. Please wait ${response.headers.get("Retry-After") || "a little while"} seconds before trying again.`);
      } else if (response.status === 400 || response.status === 422) {
        showMessage(body.error || "Please check your details and try again.");
      } else if (response.status === 404) {
        showMessage("That appointment is no longer available. Please choose another time.");
        await loadSlots();
      } else {
        showMessage("We could not complete the booking. Please try again.");
      }
      if (response.status !== 409) state.selectedSlot = selectedAtSubmit;
      return;
    }
    showConfirmation(body);
    state.selectedSlot = null;
    elements.submitBooking.textContent = "Appointment confirmed";
    await loadSlots();
  } catch {
    showMessage("The clinic service is temporarily unavailable. Please try again.");
  } finally {
    elements.submitBooking.disabled = !state.selectedSlot;
    if (state.selectedSlot) elements.submitBooking.textContent = "Confirm appointment";
  }
}

function resetForm() {
  document.querySelector("#appointments").hidden = false;
  elements.confirmation.hidden = true;
  elements.bookingForm.reset();
  elements.selectedSlot.innerHTML = '<span class="selected-placeholder-icon" aria-hidden="true">+</span><span>Select an available time<br><small>and it will appear here</small></span>';
  elements.submitBooking.disabled = true;
  elements.submitBooking.textContent = "Confirm appointment ↗";
  clearMessage();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

elements.menuToggle.addEventListener("click", () => {
  const open = elements.nav.classList.toggle("open");
  elements.menuToggle.setAttribute("aria-expanded", String(open));
});
elements.nav.addEventListener("click", () => {
  elements.nav.classList.remove("open");
  elements.menuToggle.setAttribute("aria-expanded", "false");
});
elements.providerFilter.addEventListener("change", loadSlots);
elements.bookingForm.addEventListener("submit", submitBooking);
elements.bookAnother.addEventListener("click", resetForm);
loadSlots();
