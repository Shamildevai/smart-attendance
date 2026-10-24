/* Offline UI localization runtime: translates DOM text and attributes rendered by scripts. */
(function () {
  "use strict";

  var data = window.AppI18n || null;
  if (!data) return;

  var strings = data.strings || {};
  var patterns = (data.patterns || []).map(function (item) {
    var source = item[0];
    var replacement = item[1];
    var compiled = null;
    try {
      compiled = new RegExp("^" + source + "$", "s");
    } catch (error) {
      compiled = null;
    }
    return compiled ? [compiled, replacement] : null;
  }).filter(Boolean);

  var ARABIC = /[\u0600-\u06FF]/;
  var PLACEHOLDER = /\{(\d+)\}/g;
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };
  var ATTRS = ["placeholder", "title", "aria-label", "alt"];

  function translateValue(value, depth) {
    if (!value || typeof value !== "string" || !ARABIC.test(value)) return value;
    var bounds = /^(\s*)([\s\S]*?)(\s*)$/.exec(value);
    if (!bounds) return value;
    var leading = bounds[1];
    var core = bounds[2];
    var trailing = bounds[3];
    if (!core) return value;
    var normalized = core.replace(/\s+/g, " ").trim();
    function lookup(text) {
      if (Object.prototype.hasOwnProperty.call(strings, text)) {
        return strings[text];
      }
      for (var index = 0; index < patterns.length; index += 1) {
        var match = patterns[index][0].exec(text);
        if (!match) continue;
        var replacement = patterns[index][1];
        return replacement.replace(PLACEHOLDER, function (token, position) {
          var captured = match[Number(position) + 1] || "";
          if (depth < 2 && ARABIC.test(captured)) {
            return translateValue(captured, depth + 1);
          }
          return captured;
        });
      }
      return null;
    }
    var translated = lookup(normalized);
    if (translated !== null) {
      return leading + translated + trailing;
    }
    var tail = /^(.*?)([.!?؟…]+)$/.exec(normalized);
    if (tail && tail[1].trim()) {
      translated = lookup(tail[1].trim());
      if (translated !== null) {
        return leading + translated + tail[2] + trailing;
      }
    }
    return value;
  }

  function translateTextNode(node) {
    var value = node.nodeValue;
    var translated = translateValue(value, 0);
    if (translated !== value) node.nodeValue = translated;
  }

  function translateAttributes(element) {
    for (var index = 0; index < ATTRS.length; index += 1) {
      var name = ATTRS[index];
      if (!element.hasAttribute(name)) continue;
      var value = element.getAttribute(name);
      var translated = translateValue(value, 0);
      if (translated !== value) element.setAttribute(name, translated);
    }
  }

  function walk(node, inheritedSkip) {
    if (!node) return;
    if (node.nodeType === 3) {
      if (!inheritedSkip) translateTextNode(node);
      return;
    }
    if (node.nodeType !== 1) return;
    var skip = inheritedSkip ||
      Boolean(SKIP_TAGS[node.tagName]) ||
      node.hasAttribute("data-no-i18n") ||
      node.isContentEditable;
    if (!skip) translateAttributes(node);
    var children = node.childNodes;
    for (var index = 0; index < children.length; index += 1) {
      walk(children[index], skip);
    }
  }

  function localizeExisting() {
    document.documentElement.lang = data.lang;
    document.documentElement.dir = data.dir;
    walk(document.documentElement, false);
  }

  data.translate = function (value) { return translateValue(value, 0); };
  window.translateUiText = data.translate;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", localizeExisting, { once: true });
  } else {
    localizeExisting();
  }

  if (data.lang !== "ar" && typeof MutationObserver !== "undefined") {
    var observer = new MutationObserver(function (records) {
      for (var index = 0; index < records.length; index += 1) {
        var record = records[index];
        if (record.type === "childList") {
          for (var added = 0; added < record.addedNodes.length; added += 1) {
            walk(record.addedNodes[added], false);
          }
        } else if (record.type === "characterData") {
          translateTextNode(record.target);
        } else if (record.type === "attributes") {
          translateAttributes(record.target);
        }
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRS,
    });
  }
})();
