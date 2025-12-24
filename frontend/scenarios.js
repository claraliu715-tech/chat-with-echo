document.addEventListener("DOMContentLoaded", () => {
  const cards = Array.from(document.querySelectorAll(".scenario-card"));
  const buttons = document.querySelectorAll(".scenario-btn");

  const prevBtn = document.querySelector(".nav-btn.left");
  const nextBtn = document.querySelector(".nav-btn.right");

  let current = 0;

  function updateCards(){
    cards.forEach((card, i) => {
      card.classList.remove("active", "prev", "next");

      if (i === current) {
        card.classList.add("active");
      } else if (i === current - 1) {
        card.classList.add("prev");
      } else if (i === current + 1) {
        card.classList.add("next");
      }
    });

    // Button visibility
    if (prevBtn) {
      prevBtn.classList.toggle("disabled", current === 0);
    }
    if (nextBtn) {
      nextBtn.classList.toggle("disabled", current === cards.length - 1);
    }
  }

  updateCards();

  // Arrow buttons
  prevBtn?.addEventListener("click", () => {
    if (current > 0) {
      current--;
      updateCards();
    }
  });

  nextBtn?.addEventListener("click", () => {
    if (current < cards.length - 1) {
      current++;
      updateCards();
    }
  });

  // Keyboard support (accessibility bonus)
  window.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" && current < cards.length - 1) {
      current++;
      updateCards();
    }
    if (e.key === "ArrowLeft" && current > 0) {
      current--;
      updateCards();
    }
  });

  // Practice button jump
  buttons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const scenario = btn.dataset.scenario || "general";
      localStorage.setItem("echo_scenario", scenario);
    window.location.href = new URL("index.html", window.location.href).toString();
    });
  });
});
