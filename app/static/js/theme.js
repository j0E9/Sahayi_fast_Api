// static/js/theme.js

// =============================
// SAHAYI GLOBAL THEME MANAGER
// =============================

// Apply saved theme immediately (before page paint)
(function () {
  const savedTheme = localStorage.getItem("theme");

  if (savedTheme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
})();


// Wait for DOM before binding button
document.addEventListener("DOMContentLoaded", () => {
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");

  if (!btn) return; // Page has no toggle (still apply theme)

  // Set correct icon
  const savedTheme = localStorage.getItem("theme");
  btn.textContent = savedTheme === "dark" ? "☀️" : "🌙";

  // Toggle handler
  btn.addEventListener("click", () => {
    const isDark = root.getAttribute("data-theme") === "dark";

    if (isDark) {
      root.removeAttribute("data-theme");
      localStorage.setItem("theme", "light");
      btn.textContent = "🌙";
    } else {
      root.setAttribute("data-theme", "dark");
      localStorage.setItem("theme", "dark");
      btn.textContent = "☀️";
    }
  });
});
