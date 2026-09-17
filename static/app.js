document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("parentSubscription");
  const preview = document.getElementById("familyPreview");
  if (sel && preview) {
    const update = () => {
      const o = sel.options[sel.selectedIndex];
      if (!o || !o.dataset.expiry) {
        preview.textContent = "Select a parent subscription to see the actual family expiry.";
        return;
      }
      preview.innerHTML = `<strong>Family subscription:</strong> ${o.dataset.start} → <strong>${o.dataset.expiry}</strong><br><span class="muted">This expiry is the parent subscription expiry — it is NOT calculated from Customer Added Date.</span>`;
    };
    sel.addEventListener("change", update); update();
  }
  document.querySelectorAll("input[type=date]").forEach(i => {
    i.addEventListener("change", () => {
      if (i.value) i.classList.add("has-value");
    });
  });
});