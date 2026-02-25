# Heuristics Reference

All heuristics are fully deterministic (no ML, no LLM). They operate on URL strings
and raw HTML only.

---

## 1. Article Page Scoring

**Function:** `heuristics.article_score(url: str, html: str) -> int`

Threshold: **≥ 35** → treat as article and attempt extraction.

### URL Signals

| Signal | Score | Pattern |
|--------|-------|---------|
| Known article path segment | +15 | Path contains `/blog/`, `/post/`, `/posts/`, `/article/`, `/articles/`, `/news/`, `/story/`, `/stories/`, `/essay/`, `/essays/`, `/journal/`, `/write/`, `/p/` |
| Date in URL path | +10 | `\d{4}/\d{2}(/\d{2})?` in path |
| Long slug (≥4 path segments) | +5 | e.g. `/en/blog/2024/my-great-post` |
| Single-segment path (home/about/etc) | −20 | Path depth ≤ 1 |
| Excluded path pattern | −30 | Path contains `/tag/`, `/tags/`, `/category/`, `/categories/`, `/search`, `/login`, `/signup`, `/register`, `/privacy`, `/terms`, `/contact`, `/about`, `/archive`, `/archives/`, `/feed`, `/rss`, `/sitemap` |
| Paginated URL | −15 | Path ends with `/page/\d+` or query `page=\d+` |
| Author listing | −10 | Path contains `/author/` with no further slug |

### Content Signals (applied to parsed HTML)

| Signal | Score | Detection |
|--------|-------|-----------|
| Word count > 300 | +20 | After stripping nav/header/footer |
| Word count 150–300 | +10 | Same |
| Exactly one `<h1>` | +15 | `soup.find_all('h1')` length == 1 |
| Author in metadata | +10 | OG, JSON-LD, twitter:creator, `<meta name="author">`, byline pattern |
| Published date in metadata | +10 | Any date meta tag or JSON-LD datePublished |
| JSON-LD type is article-like | +10 | `@type` in {Article, BlogPosting, NewsArticle, TechArticle, ScholarlyArticle} |
| OG type is article | +5 | `og:type == "article"` |
| Paragraph count > 3 | +5 | `<p>` elements with ≥ 20 chars each |
| Many outbound links (> 20) | −10 | Likely a listing/navigation page |
| `rel="next"` or `rel="prev"` | −15 | Pagination; even if it has content |
| Very short text (< 50 words) | −20 | After stripping boilerplate |

### Score Examples

| Page type | Typical score |
|-----------|--------------|
| Full blog post with metadata | 70–90 |
| Short blog post, no metadata | 40–55 |
| Blog listing/index page | 5–25 |
| Tag/category page | −30 to −5 |
| Home page | −20 to 10 |
| Paginated post list | −20 to 0 |

---

## 2. Adaptive Page Classification

**Module:** `llmparser/extractors/adaptive.py`

**Functions:**
- `_detect_signals(html, url) → PageSignals`
- `classify_page(html, url) → ClassificationResult`
- `adaptive_fetch_html(url) → AdaptiveFetchResult`

### PageSignals

Computed from a single HTML parse:

| Signal | Type | How computed |
|--------|------|--------------|
| `body_word_count` | `int` | Words in `<body>` after stripping `<nav>`, `<header>`, `<footer>`, `<script>`, `<style>`, `<template>` |
| `is_js_spa` | `bool` | JS framework root div present + sparse text, OR ultra-thin body (<10 words) + external scripts |
| `is_cookie_walled` | `bool` | Cookie/GDPR overlay keywords detected in visible text |
| `is_paywalled` | `bool` | Paywall keywords + metered content signals |
| `frameworks_detected` | `list[str]` | Named JS frameworks identified from fingerprints |
| `amp_url` | `str\|None` | `<link rel="amphtml" href="...">` |
| `feed_url` | `str\|None` | `<link rel="alternate" type="application/rss+xml">` |

### JS SPA Detection (two paths)

**Path A — Framework fingerprints** (higher confidence):

```python
_JS_FRAMEWORK_FINGERPRINTS = {
    "Next.js":   [("script", "src", "/_next/"), ("div", "id", "__next")],
    "React":     [("div", "id", "root"), ("div", "id", "react-root")],
    "Vue":       [("div", "id", "app"), ("div", "id", "__nuxt")],
    "Angular":   [("app-root", None, None), ("div", "ng-app", None)],
    "Gatsby":    [("div", "id", "gatsby-focus-wrapper")],
    "Svelte":    [("div", "id", "svelte")],
    "Ember":     [("div", "id", "ember"), ("body", "class", "ember-application")],
}
# Triggered when ANY fingerprint matches AND body_word_count < 200
```

**Path B — Ultra-thin body** (catches custom builds, VFS Global-style apps):

```python
# Triggered when:
body_word_count < 10  AND  bool(soup.find("script", src=True))
# No known framework required
```

### Classification Rules (in priority order)

| Condition | PageType | Strategy | Confidence |
|-----------|----------|----------|------------|
| `is_cookie_walled` | `COOKIE_WALLED` | `playwright` | 0.85 |
| `is_paywalled` | `PAYWALLED` | `playwright` | 0.75 |
| `is_js_spa` AND `amp_url` present | `JS_SPA` | `amp` | 0.90 |
| `is_js_spa` with named framework | `JS_SPA` | `playwright` | 0.90 |
| `is_js_spa` (ultra-thin path) | `JS_SPA` | `playwright` | 0.80 |
| `body_word_count >= 150` | `STATIC_HTML` | `static` | 0.90 |
| `body_word_count >= 50` | `STATIC_HTML` | `mobile_ua` | 0.70 |
| All else | `UNKNOWN` | `static_best_effort` | 0.50 |

### Adaptive Strategy Flow

```
adaptive_fetch_html(url)
    │
    ├─ 1. Fetch HTML via urllib (static)
    ├─ 2. classify_page(html, url)
    │
    ├─ strategy == "static"          → return html as-is
    ├─ strategy == "amp"             → fetch amp_url via urllib
    ├─ strategy == "mobile_ua"       → re-fetch with mobile User-Agent
    ├─ strategy == "playwright"      → _fetch_html_playwright(url)  [4-phase]
    └─ strategy == "static_best_effort" → return html with partial content warning
```

---

## 3. JS Rendering Detection (Spider heuristic)

**Function:** `heuristics.needs_js(html: str, threshold_words: int = 100) -> bool`

Used by the Scrapy spider (not the adaptive engine). Returns `True` if the page almost certainly requires JavaScript.

### Detection Signals (any one triggers True)

#### Signal 1: Explicit JavaScript-required message
```
"enable javascript" in body_text.lower()
"javascript is required" in body_text.lower()
"please enable javascript" in body_text.lower()
"javascript must be enabled" in body_text.lower()
"this site requires javascript" in body_text.lower()
"you need to enable javascript" in body_text.lower()
```

#### Signal 2: JS framework root + sparse text
```python
JS_ROOT_SELECTORS = [
    '#__next',        # Next.js
    '#app',           # Vue / generic
    '#root',          # React
    '#__nuxt',        # Nuxt.js
    '#app-root',      # Angular
    '#gatsby-focus-wrapper',  # Gatsby
    '[data-reactroot]',
    '[data-server-rendered]',
    'div[ng-app]',
]
# AND word_count < threshold_words (default 100)
```

#### Signal 3: Heavy scripts + near-empty body
```python
script_count = len(soup.find_all('script', src=True))
# True if: script_count > 8 AND word_count < 50
```

#### Signal 4: Meaningful noscript content
```python
for noscript in soup.find_all('noscript'):
    if len(noscript.get_text().split()) > 20:
        return True
```

### False positive mitigation

- Strip `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>` BEFORE counting words
- Signal 2 requires BOTH the JS root AND sparse text (not just the root div)
- Signal 3 requires a high script count (>8 external scripts)
- Signal 1 is the most reliable; Signal 3 is most prone to false positives on ad-heavy pages

---

## 4. Content Extraction Cascade

**Module:** `llmparser/extractors/main_content.py`

### Pre-processing (before any extractor)

1. **`<template>` removal** — regex strip before BS4 parsing (lxml re-parents template children into body; `decompose()` alone is insufficient)
2. **Cookie-consent removal** — 30+ named CSS selectors (CookieYes, Cookiebot, OneTrust, Complianz, Borlabs, WP GDPR, generic) plus keyword sweep for dynamically-named widgets

### Extractor Thresholds

| Extractor | Minimum words to be considered successful |
|-----------|------------------------------------------|
| readability-lxml | 50 |
| trafilatura | 30 |
| DOM heuristic | 10 |

### Best-of-Two Selection Logic

```python
r_wc = word_count(readability_output)
t_wc = word_count(trafilatura_output)

if r_wc >= 50 and t_wc >= 30:
    if t_wc >= r_wc * 1.4:
        return trafilatura   # ≥40% more words → prefer trafilatura
    return readability       # readability wins otherwise

if r_wc >= 50: return readability
if t_wc >= 30: return trafilatura

# Both failed → DOM heuristic
```

**Why 1.4×?** Avoids switching on minor noise differences. Trafilatura wins only when it meaningfully outperforms readability — typically on multi-section service pages where readability fixates on one content block.

### DOM Heuristic: Full-Body Fallback Rule

After paragraph-density scoring of all `<div>`/`<section>` elements:

```python
top_wc = len(top_element.get_text().split())
body_wc = len(body.get_text().split())

if top_wc / body_wc >= 0.55:
    return str(top_element)   # One element dominates: return it
else:
    return str(body)          # Content spread equally: return full body
```

This prevents missing content on pages where multiple sections carry equal weight (wikis, service portals, documentation pages).

---

## 5. Fallback DOM Selection

**Function:** `main_content.dom_heuristic_extract(html: str) -> str`

Applied when readability-lxml AND trafilatura both fail or return < threshold words.

### Step 1: Priority Selector Cascade

Try these CSS selectors in order; pick the first match with ≥ 10 words:

```
article
main
[role="main"]
[itemprop="articleBody"]
.post-content
.article-content
.entry-content
.post-body
.article-body
#article-content
#post-content
#entry-content
#content
#main-content
.content-body
.story-body
.blog-post
.post
.single-content
```

Before selection, decompose (remove from DOM):
- `<nav>`, `<header>`, `<footer>`, `<aside>`
- `<script>`, `<style>`, `<noscript>`
- `<form>`, `<button>`, `<input>`, `<select>`, `<textarea>`
- Elements with class/id containing: `sidebar`, `comment`, `advertisement`, `banner`, `promo`, `related`, `share`, `social`, `newsletter`, `cookie`, `popup`, `modal`, `widget`

### Step 2: Paragraph Density Scoring

If no selector match, score all `<div>` and `<section>` elements:

```
paragraph_words = sum(len(p.get_text().split()) for p in el.find_all('p'))
total_words     = len(el.get_text().split())
density         = paragraph_words / max(total_words, 1)
score           = paragraph_words * density
```

Pick the highest-scoring element with ≥ 10 paragraph words, then apply the 55% body-dominance rule (see §4 above).

### Step 3: Body Fallback

If nothing found with ≥ 10 words, return the full `<body>` element.
If no body, return the original HTML unchanged.

---

## 6. URL Filtering Rules

### Hard-exclude (never crawled, even for link discovery)

```
/_next/static/
/cdn-cgi/
/wp-content/uploads/
/__webpack
/wp-json/
/wp-admin/
/xmlrpc.php
.amp(?|$)      ← AMP duplicate URLs
```

### Soft-exclude (crawled for links, but scored low for extraction)

Applied via article scoring penalties (see §1):
```
/tag/   /tags/   /category/   /categories/
/search  /login   /signup   /register
/privacy  /terms   /contact   /about
/archive  /archives  /feed  /rss  /sitemap
```

### Excluded by extension

`.pdf`, `.jpg`, `.jpeg`, `.png`, `.gif`, `.svg`, `.webp`,
`.css`, `.js`, `.woff`, `.woff2`, `.ttf`, `.ico`,
`.zip`, `.tar`, `.gz`

Optional `--exclude-regex` applied after all built-in rules.

---

## 7. Reading Time Calculation

```python
WORDS_PER_MINUTE = 200
reading_time_minutes = max(1, math.ceil(word_count / WORDS_PER_MINUTE))
```

---

## 8. Language Detection

Determined in priority order:
1. `<html lang="...">` attribute
2. `og:locale` (converts `en_US` → `en`)
3. JSON-LD `inLanguage` field
4. `<meta http-equiv="content-language">` or `<meta name="language">`
5. `None` if undetermined (do not guess)

---

## 9. Content Deduplication

**Pipeline:** `ContentHashDedupPipeline` (priority 100, before validation)

```python
digest = hashlib.sha256(content_text[:5_000].encode()).hexdigest()[:16]
# Drop if digest already seen this crawl session
```

- Only applied when `len(content_text) >= 100` (too-short content is left to the validation pipeline)
- Catches syndicated posts and canonical URL mismatches
- Hash is a 16-char SHA-256 prefix (1-in-2^64 collision probability)
