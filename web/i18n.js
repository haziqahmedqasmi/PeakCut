/* Static page copy is translated once. Rendered UI uses t() or html() at the
   message site so names, captions and other user content can pass through. */
const i18n = (() => {
  const key = "peakcut_language";
  let locale = "en";
  try {
    if (localStorage.getItem(key) === "pt-BR") locale = "pt-BR";
  } catch {}
  const messages = locale === "pt-BR" && typeof peakcutPortuguese !== "undefined" ? peakcutPortuguese : {};
  const lookup = message => Object.hasOwn(messages, message) ? messages[message] : message;
  const substitute = (message, values) => values.length
    ? message.replace(/\{(\d+)\}/g, (match, index) => values[index] ?? match)
    : message;
  const t = (message, ...values) => substitute(lookup(message), values);
  const serverMessages = [
    [/^(\d+) clips? ready$/, "{0} clip ready", "{0} clips ready"],
    [/^That file is too big\. The limit is (\d+) MB\.$/, "That file is too big. The limit is {0} MB."],
    [/^You already have (\d+) uploads in progress\. Let one finish first\.$/, "You already have {0} uploads in progress. Let one finish first."],
    [/^That link isn't a (YouTube|Twitch|Kick) channel\.$/, "That link isn't a {0} channel."],
    [/^Waiting in line: (\d+) videos? ahead of yours$/, "Waiting in line: {0} video ahead of yours", "Waiting in line: {0} videos ahead of yours"],
    [/^High demand right now: (\d+) videos? ahead of yours$/, "High demand right now: {0} video ahead of yours", "High demand right now: {0} videos ahead of yours"],
  ];
  function detail(message) {
    if (locale !== "pt-BR" || typeof message !== "string") return message;
    const translated = lookup(message);
    if (translated !== message) return translated;
    const progress = message.match(/^(Reading the transcript|Writing the transcript|Finding the best moments) \(minute (\d+) of (\d+)\)$/);
    if (progress) return `${t(progress[1])} ${t("(minute {0} of {1})", progress[2], progress[3])}`;
    for (const [pattern, singular, plural] of serverMessages) {
      const match = message.match(pattern);
      if (match) return t(plural && match[1] !== "1" ? plural : singular, ...match.slice(1));
    }
    return message;
  }
  const escape = text => text.replace(/[&<>"']/g, char => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
  })[char]);
  // Interpolations come from the original template, which already escapes user data.
  const html = (message, ...values) => substitute(escape(lookup(message)), values);
  const picker = () => `<select data-language aria-label="${html("Interface language")}">
    <option value="en" lang="en" ${locale === "en" ? "selected" : ""}>English</option>
    <option value="pt-BR" lang="pt-BR" ${locale === "pt-BR" ? "selected" : ""}>Português (Brasil)</option>
  </select>`;
  const nextLanguage = locale === "en" ? "pt-BR" : "en";
  function toggleButton(compact = false) {
    const label = html(nextLanguage === "pt-BR" ? "Switch to Portuguese (Brazil)" : "Switch to English");
    const code = nextLanguage.toUpperCase();
    return `<button type="button" class="btn${compact ? " sm" : ""}" data-language="${nextLanguage}"
      aria-label="${compact ? `${code} — ${label}` : label}" title="${label}">${compact ? code : label}</button>`;
  }

  function setLanguage(value) {
    if (!["en", "pt-BR"].includes(value) || value === locale) return false;
    try {
      localStorage.setItem(key, value);
    } catch {
      return false;
    }
    location.reload();
    return true;
  }

  function localizePage() {
    if (locale === "pt-BR") {
      const translate = text => text.replace(/\S[\s\S]*?(?=\s*$)/, part => {
        const normalized = part.replace(/\s+/g, " ");
        const translated = lookup(normalized);
        return translated === normalized ? part : translated;
      });
      document.documentElement.lang = locale;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.parentElement.closest("script, style, code, kbd, samp, [data-i18n-skip]")) {
          node.data = translate(node.data);
        }
      }
      for (const name of ["aria-label", "title", "placeholder", "alt"]) {
        document.querySelectorAll(`[${name}]`).forEach(element => {
          element.setAttribute(name, translate(element.getAttribute(name)));
        });
      }
      for (const element of document.querySelectorAll("[data-i18n-law]")) {
        const country = element.textContent.match(/^the laws of (.+)$/);
        if (country) element.textContent = t("the laws of {0}", country[1]);
      }
      document.title = translate(document.title);
    }
    document.querySelectorAll("[data-language-picker]").forEach(element => {
      element.innerHTML = picker();
    });
  }

  document.addEventListener("change", event => {
    const select = event.target.closest("select[data-language]");
    if (select && !setLanguage(select.value)) select.value = locale;
  });
  document.addEventListener("click", event => {
    const button = event.target.closest("button[data-language]");
    if (button) setLanguage(button.dataset.language);
  });
  localizePage();

  // English keeps the browser's existing number and date formatting.
  return {t, html, detail, toggleButton, formatLocale: locale === "pt-BR" ? locale : undefined};
})();
