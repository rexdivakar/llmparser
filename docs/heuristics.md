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
| Excluded path pattern | −30 | Path contains `/tag/`, `/tags/`, `/category/`, `/categories/`, `/search`, `/login`, `/signup`, `/register`, `/privacy`, `/terms`, `/contact`, `/about`, `/archive`, `/archives`, `/feed`, `/rss`, `/sitemap` |
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

## 2. JS Rendering Detection

**Function:** `heuristics.needs_js(html: str, threshold_words: int = 100) -> bool`

Returns `True` if the page almost certainly requires JavaScript to render its content.

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

has_js_root = any(soup.select(sel) for sel in JS_ROOT_SELECTORS)
# AND text is sparse (after removing scripts/styles/nav/header/footer)
word_count < threshold_words (default 100)
```

#### Signal 3: Heavy scripts + near-empty body

```python
script_count = len(soup.find_all('script', src=True))
script_count > 8 AND word_count < 50
```

#### Signal 4: Meaningful noscript content

```python
for noscript in soup.find_all('noscript'):
    if len(noscript.get_text().split()) > 20:
        return True  # noscript has actual message content
```

### False positive mitigation

- Strip `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>` BEFORE counting words
- Signal 2 requires BOTH the JS root AND sparse text (not just the root div)
- Signal 3 requires a high script count (>8 external scripts)
- Signal 1 is the most reliable; Signal 3 is most prone to false positives on ad-heavy pages

---

## 3. Fallback DOM Selection

**Function:** `main_content.dom_heuristic_extract(html: str) -> str`

Applied when readability-lxml AND trafilatura both fail or return < 30 words.

### Step 1: Priority Selector Cascade

Try these CSS selectors in order; pick the first match with ≥ 50 words:

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
```

Before selection, decompose (remove from DOM):
- `<nav>`, `<header>`, `<footer>`, `<aside>`
- `<script>`, `<style>`, `<noscript>`
- `<form>`, `<button>`, `<input>`
- Elements with class/id containing: `sidebar`, `comment`, `advertisement`, `ad-`, `promo`, `related`, `share`, `social`, `newsletter`

### Step 2: Paragraph Density Scoring

If no selector match, score all `<div>` and `<section>` elements:

```
paragraph_words = sum(len(p.get_text().split()) for p in el.find_all('p'))
total_words     = len(el.get_text().split())
density         = paragraph_words / max(total_words, 1)
score           = paragraph_words * density
```

Pick the element with the highest score that has ≥ 50 paragraph words.

Exclude elements that are ancestors of already-chosen elements (de-nest).

### Step 3: Body Fallback

If nothing found with ≥ 50 words, return the full `<body>` element.
If no body, return the original HTML unchanged.

---

## 4. URL Filtering Rules

### Include (crawl this URL)

- Same domain/host as `start_url`
- Content-type is HTML (determined by extension or response header)
- Optional `--include-regex` matches

### Exclude (skip without processing)

Excluded if path contains any of:
```
/tag/   /tags/   /category/   /categories/
/search  /login   /signup   /register
/privacy  /terms   /contact   /about  (only if exact path, not prefix)
/archive  /archives  /feed  /rss  /sitemap
/cdn-cgi/  /__webpack  /_next/static  /wp-content/uploads
```

Excluded by extension: `.pdf`, `.jpg`, `.jpeg`, `.png`, `.gif`, `.svg`, `.webp`,
`.css`, `.js`, `.woff`, `.woff2`, `.ttf`, `.ico`, `.xml` (unless sitemap),
`.zip`, `.tar`, `.gz`

Optional `--exclude-regex` applied after built-in rules.

---

## 5. Reading Time Calculation

```python
WORDS_PER_MINUTE = 200
reading_time_minutes = max(1, math.ceil(word_count / WORDS_PER_MINUTE))
```

---

## 6. Language Detection

Determined in priority order:
1. `<html lang="...">` attribute
2. `og:locale` (convert `en_US` → `en`)
3. JSON-LD `inLanguage` field
4. `<meta http-equiv="content-language">` or `<meta name="language">`
5. `None` if undetermined (do not guess)
