/**
 * Family Christmas Gift Exchange - Interactive UI Utilities
 * Vanilla JS, no external libraries.
 */

document.addEventListener("DOMContentLoaded", () => {

  // Explain assignment strategies right where they are chosen.
  // Examples use fictional A-E households and NEVER expose the real draw.
  const methods = {
    shuffled_rotation: {
      summary: "Shuffle once, then repeat the same cycle. Everyone gives to every other family.",
      example: "With five families: A gives to B, C, D, E over four years, then starts with B again.",
      warning: false
    },
    fixed_rotation: {
      summary: "You choose the family order and rotation steps. The cycle then repeats unchanged.",
      example: "With families A-B-C-D-E and steps 1, 3, 2, 4: A gives to B, D, C, E, then B again.",
      warning: false
    },
    uniform_random: {
      summary: "Make a fresh random matching each year. Recent-repeat rules still apply, but complete coverage is not guaranteed.",
      example: "A might give to D this year and B next year; C could go several years without being selected.",
      warning: true
    },
    randomized_cycle: {
      summary: "Shuffle separately within each block of years. Each block covers everyone, but repeats can happen where blocks meet.",
      example: "A might give to B in the final year of one block and B again in the first year of the next.",
      warning: true
    }
  };
  document.querySelectorAll("[data-method-selector]").forEach((selector) => {
    const form = selector.closest("form");
    const explainer = form && form.querySelector("[data-method-explainer]");
    const fixed = form && form.querySelector("[data-fixed-config]");
    const coverage = form && form.querySelector('input[name="enforce_sliding_coverage"]');
    const update = () => {
      const description = methods[selector.value] || methods.shuffled_rotation;
      if (explainer) {
        explainer.replaceChildren();
        const summary = document.createElement("p");
        summary.textContent = description.summary;
        const example = document.createElement("small");
        const lead = document.createElement("strong");
        lead.textContent = "Example: ";
        example.append(lead, document.createTextNode(description.example));
        explainer.append(summary, example);
        if (description.warning && coverage && coverage.checked) {
          const notice = document.createElement("p");
          notice.className = "ui-method-conflict";
          notice.textContent = "To choose this method, turn off 'Guarantee full coverage' above. The app will not quietly relax that rule.";
          explainer.append(notice);
        }
        explainer.classList.toggle("method-explainer-warning", description.warning);
      }
      if (fixed) {
        const show = selector.value === "fixed_rotation";
        fixed.hidden = !show;
        if (show) fixed.open = true;
      }
    };
    selector.addEventListener("change", update);
    if (coverage) coverage.addEventListener("change", update);
    update();
  });

  // Jump to rarely used configuration only when the organizer asks for it.
  document.querySelectorAll("[data-open-config]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.openConfig);
      if (!target) return;
      target.open = true;
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      const firstInput = target.querySelector("select, input:not([type=hidden])");
      if (firstInput) {
        firstInput.focus({ preventScroll: true });
      }
    });
  });

  // In-place history spoiler updates: no navigation, reset, or scroll jump.
  // Keep the anchor (#year-2024) intact so historical years are linkable.
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-ajax-spoilers]");
    if (!form) return;
    event.preventDefault();

    const shell = document.getElementById("history-shell");
    if (!shell) {
      form.submit(); // Standard safe redirect if embedded on an unsupported page.
      return;
    }

    const button = form.querySelector("button[type=submit]");
    const oldY = window.scrollY;
    const oldHash = location.hash;
    if (button) button.disabled = true;
    shell.setAttribute("aria-busy", "true");
    try {
      const result = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json"
        },
        redirect: "manual"
      });
      if (!result.ok || !(result.headers.get("Content-Type") || "").includes("json")) {
        throw new Error("Spoiler update was not accepted.");
      }
      const payload = await result.json();
      if (!payload.ok) throw new Error("Spoiler change failed.");

      const refreshed = await fetch(location.pathname + location.search, {
        credentials: "same-origin",
        headers: { "Accept": "text/html" },
        cache: "no-store"
      });
      if (!refreshed.ok || !refreshed.headers.get("Content-Type")?.includes("html")) {
        throw new Error("Could not refresh historical results.");
      }
      const documentCopy = new DOMParser().parseFromString(
        await refreshed.text(), "text/html"
      );
      const newShell = documentCopy.getElementById("history-shell");
      if (!newShell) throw new Error("History view no longer available.");
      shell.replaceWith(newShell);
      // URL and fragment stay unchanged. No full page navigation.
      if (oldHash && location.hash !== oldHash) {
        history.replaceState(null, "", location.pathname + location.search + oldHash);
      }
      window.scrollTo({ top: oldY, behavior: "instant" });
      const nextButton = newShell.querySelector("form[data-ajax-spoilers] button");
      if (nextButton) nextButton.focus({ preventScroll: true });
    } catch (error) {
      console.error("Unable to update spoilers in place:", error);
      // Failure fallback remains on the History route, rather than going to Dashboard.
      form.submit();
    } finally {
      shell.removeAttribute("aria-busy");
      if (button) button.disabled = false;
    }
  });

  // 1. Copy to clipboard handlers
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async (e) => {
      e.preventDefault();
      const targetSelector = button.getAttribute("data-copy-target");
      const targetElem = document.querySelector(targetSelector);
      const textToCopy = targetElem ? (targetElem.value || targetElem.textContent) : button.getAttribute("data-copy-text");

      if (!textToCopy) return;

      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(textToCopy);
        } else {
          // Fallback for non-https or older contexts
          const textarea = document.createElement("textarea");
          textarea.value = textToCopy;
          textarea.style.position = "fixed";
          textarea.style.opacity = "0";
          document.body.appendChild(textarea);
          textarea.focus();
          textarea.select();
          document.execCommand("copy");
          document.body.removeChild(textarea);
        }

        const originalText = button.textContent;
        button.textContent = "Copied! ✓";
        button.classList.add("btn-forest");

        setTimeout(() => {
          button.textContent = originalText;
          button.classList.remove("btn-forest");
        }, 2000);
      } catch (err) {
        console.error("Failed to copy:", err);
      }
    });
  });

  // 2. Destructive confirmation prompts
  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("click", (e) => {
      const message = element.getAttribute("data-confirm");
      if (!confirm(message || "Are you sure you wish to proceed?")) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });

  // 3. Flash alert dismissal
  document.querySelectorAll(".alert-close").forEach((closeBtn) => {
    closeBtn.addEventListener("click", () => {
      const alert = closeBtn.closest(".alert");
      if (alert) {
        alert.style.opacity = "0";
        setTimeout(() => alert.remove(), 200);
      }
    });
  });
});
