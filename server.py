"""Prism Media MCP connector (read-only).

Lets an AI agent search Prism Media's courses, read course details and
curricula, browse the blog, meet the faculty, read approved testimonials and
get a recommended course path. Backed by the public Flask API (/api/v1).
Nothing is ever written to the site; enquiries are only returned as links.

Env vars:
  PRISM_API_URL      API base (default https://prismmedia.org/api/v1)
  PRISM_SITE_URL     Public site, used for links (default https://prismmedia.org)
  MCP_TRANSPORT      stdio (default) | streamable-http
  MCP_HOST / MCP_PORT  for streamable-http (default 0.0.0.0 / 8000)
"""
import json
import os
import re
import time

from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

API = os.getenv("PRISM_API_URL", "https://prismmedia.org/api/v1").rstrip("/")
SITE = os.getenv("PRISM_SITE_URL", "https://prismmedia.org").rstrip("/")
CACHE_TTL = 300
MAX_POST_CHARS = 6000
LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}
STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "in", "for", "on", "with", "i", "want",
    "learn", "how", "do", "me", "my", "be", "become", "get", "job", "as", "is",
    "course", "courses", "about", "from", "into", "that", "can", "should", "what",
    "best", "institute", "bhubaneswar",
}

INSTITUTE = {
    "name": "Prism Media",
    "tagline": "Creative education institute for Animation, VFX, Design, AI and Digital Media",
    "founded": 2014,
    "operated_by": "A unit of Swami Chidananda Institute of Social Science",
    "affiliations": ["Utkal University of Culture (Govt. of Odisha)", "Lingaraj Vidyapeeth", "Skill India (Applied AI Program)"],
    "highlights": {
        "placement_rate": "97% (2024, as stated on the site)",
        "students_trained": "1,798+ (site counter)",
        "industry_partners": "60+",
        "active_mentors": "30+",
        "years": "12+",
    },
    "address": "Plot No.20, Acharya Vihar Square, Near Acharya Vihar Bus Stop, Bhubaneswar - 751022, Odisha",
    "phone": "+91 94371 52172",
    "toll_free": "1800 891 9233",
    "email": "infoprismmediaodisha@gmail.com",
    "whatsapp": "https://wa.me/919437152172",
    "hostel": "Available",
    "social": {
        "facebook": "https://www.facebook.com/prismmediaodisha",
        "instagram": "https://www.instagram.com/prismmediaanimation/",
        "youtube": "https://www.youtube.com/channel/UCpMNLs5oQgSD5XXPiqdAl1w",
    },
    "pages": {
        "about": "/about-us", "gallery": "/gallery", "careers": "/careers", "contact": "/contact-us",
        "blog": "/blog", "talent_hub": "/talenthub", "testimonials": "/student-testimonials",
        "certificates": "/students-certificates", "privacy": "/privacy-policy-prismmedia",
        "terms": "/terms-conditions",
    },
    "note": "Figures are as published on the website's home page and may change; confirm fees and admission details with the admissions team.",
}

# Real page paths on prismmedia.org, keyed by course name (from the site's course menu).
# Database slugs don't always match the website URLs, so links are built from the name.
SITE_PATHS = {
    "bachelor of interior design": "/courses/bachelor-of-interior-design-bid",
    "b.sc (voc) in multimedia and animation": "/courses/bsc-in-animation-and-multimedia",
    "diploma in multimedia and animation": "/courses/diploma-in-animation-digital-media",
    "ug diploma in multimedia & animation": "/courses/adv-diploma-in-multimedia-animation-and-vfx",
    "diploma in film editing and vfx": "/courses/diploma-in-film-editing-and-vfx",
    "professional in broadcast design": "/courses/professional-in-broadcast-design",
    "professional in animation and filmmaking": "/courses/professional-in-animation-filmmaking",
    "professional in animation & vfx": "/courses/professional-in-animation-vfx",
    "professional in digital marketing & digital media": "/courses/professional-in-digital-marketing-digital-media",
    "certification in ui/ux design": "/courses/certification-in-ui-ux-design",
    "certification in photography": "/courses/certification-in-photography",
    "certification in graphics design": "/courses/certification-in-graphics-design",
    "certification in photo & video editing": "/courses/certification-in-video-editing",
    "certification in graphics & editing": "/courses/certification-in-graphics-editing",
    "certification in digital marketing": "/courses/certification-in-digital-marketing",
    "specialization 3d architect visualization": "/courses/specialization-3d-architect-visualization",
    "specialization in motion graphics design": "/courses/specialization-in-video-editing-motion-graphics",
    "applied ai program": "/courses/applied-ai-program",
    "game development": "/game",
}


def _course_url(c: dict) -> str:
    name = re.sub(r"\s+", " ", (c.get("name") or "")).strip().lower()
    path = SITE_PATHS.get(name) or f"/courses/{c.get('slug')}"
    return f"{SITE}{path}"


mcp = FastMCP(
    "prism-media",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT") or os.getenv("PORT") or "8000"),  # PORT is set by Render/Heroku
)

_cache: dict = {}


# ---------- helpers ----------
def _get(path: str, params: dict | None = None):
    key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        r = client.get(f"{API}{path}", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    _cache[key] = (now, data)
    return data


def _maybe_json(value):
    """Several text columns (key_points, syllabus, section topics) hold JSON strings."""
    if isinstance(value, str):
        v = value.strip()
        if v[:1] in "[{":
            try:
                return json.loads(v)
            except ValueError:
                pass
    return value


def _flat(value) -> str:
    """Any JSON/text field -> one searchable string."""
    value = _maybe_json(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _tokens(text: str) -> list:
    words = re.findall(r"[a-z0-9\+\#\.]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _all_courses() -> list:
    data = _get("/courses", {"per_page": 200}) or {}
    return [c for c in (data.get("data") or []) if c.get("is_active", True)]


def _score(c: dict, tokens: list):
    fields = (
        (3, c.get("name") or ""),
        (2, c.get("short_description") or ""),
        (2, _flat(c.get("key_points"))),
        (1, _flat(c.get("what_you_learn"))),
        (1, c.get("description") or ""),
        (1, _flat(c.get("syllabus"))),
    )
    score, matched = 0, set()
    for weight, text in fields:
        low = text.lower()
        for t in tokens:
            if t in low:
                score += weight
                matched.add(t)
    return score, sorted(matched)


def _summary(c: dict) -> dict:
    return {
        "slug": c.get("slug"),
        "name": c.get("name"),
        "short_description": c.get("short_description"),
        "duration": c.get("duration"),
        "level": c.get("level"),
        "language": c.get("language"),
        "category_id": c.get("category_id"),
        "certificate_available": c.get("certificate_available"),
        "is_popular": bool(c.get("is_popular")),
        "url": _course_url(c),
    }


def _trim(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


# ---------- course tools ----------
@mcp.tool()
def search_courses(query: str = "", category_id: int = 0, limit: int = 10) -> dict:
    """Search Prism Media courses by topic, skill or job role.

    Args:
        query: Free text, e.g. "3d animation", "ui ux", "digital marketing", "AI automation".
        category_id: Optional category id (see list_course_categories). 0 = any.
        limit: Max results (default 10).
    """
    try:
        courses = _all_courses()
    except httpx.HTTPError as e:
        return {"error": f"Course API unavailable: {e}"}
    if category_id:
        courses = [c for c in courses if c.get("category_id") == category_id]
    tokens = _tokens(query)
    if tokens:
        scored = [(_score(c, tokens)[0], c) for c in courses]
        courses = [c for s, c in sorted(scored, key=lambda x: -x[0]) if s > 0]
    return {"count": len(courses[:limit]), "courses": [_summary(c) for c in courses[:limit]]}


@mcp.tool()
def get_course(slug: str) -> dict:
    """Get a course's full details: overview, duration, what you learn, key points, syllabus, curriculum sections.

    Args:
        slug: Course slug from search_courses (e.g. "applied-ai-program").
    """
    try:
        data = _get(f"/courses/slug/{slug}")
    except httpx.HTTPError as e:
        return {"error": f"Course API unavailable: {e}"}
    course = (data or {}).get("data") if isinstance(data, dict) else None
    if not course:
        return {"error": f"No course found with slug '{slug}'. Use search_courses to find one."}

    sections = [
        {
            "order": s.get("order"),
            "title": s.get("title"),
            "duration": s.get("duration"),
            "topics": _maybe_json(s.get("topics")),
        }
        for s in sorted(course.get("sections") or [], key=lambda s: s.get("order") or 0)
    ]
    out = _summary(course)
    out.update({
        "description": course.get("description"),
        "what_you_learn": _maybe_json(course.get("what_you_learn")),
        "key_points": _maybe_json(course.get("key_points")),
        "syllabus": _maybe_json(course.get("syllabus")),
        "curriculum": sections,
        "fees": {
            "price": course.get("price"),
            "discount_price": course.get("discount_price"),
            "note": "Confirm current fees with admissions; a price of 0 usually means not published.",
        },
        "enquire": f"{SITE}/contact-us",
    })
    return out


@mcp.tool()
def list_course_categories() -> dict:
    """List course categories (id, name, slug) for use as a filter in search_courses."""
    try:
        data = _get("/courses/categories") or {}
    except httpx.HTTPError as e:
        return {"error": f"Course API unavailable: {e}"}
    return {"categories": data.get("data") or []}


@mcp.tool()
def recommend_path(goal: str, max_courses: int = 3) -> dict:
    """Recommend an ordered set of courses for a learner's goal.

    Matches the goal against course names, descriptions, key points and syllabus,
    then orders results from beginner to advanced (shorter courses first).

    Args:
        goal: e.g. "become a VFX artist", "start a career in UI/UX", "learn generative AI".
        max_courses: Max courses in the path (default 3).
    """
    tokens = _tokens(goal)
    if not tokens:
        return {"error": "Please describe the goal in a few words."}
    try:
        courses = _all_courses()
    except httpx.HTTPError as e:
        return {"error": f"Course API unavailable: {e}"}
    ranked = []
    for c in courses:
        score, matched = _score(c, tokens)
        if score > 0:
            ranked.append((score, matched, c))
    ranked.sort(key=lambda x: -x[0])
    top = ranked[:max_courses]
    if not top:
        return {"goal": goal, "path": [], "note": "No close match. Try search_courses with broader keywords."}
    top.sort(key=lambda x: LEVEL_ORDER.get((x[2].get("level") or "").lower(), 9))
    path = []
    for step, (_, matched, c) in enumerate(top, start=1):
        item = _summary(c)
        item.update({"step": step, "matched_on": matched})
        path.append(item)
    return {"goal": goal, "path": path,
            "note": "Use get_course for the curriculum and details of each step."}


# ---------- blog tools ----------
@mcp.tool()
def list_blog_posts(search: str = "", category_slug: str = "", page: int = 1, per_page: int = 9) -> dict:
    """List published blog posts (title, slug, excerpt, category, date).

    Args:
        search: Optional text to match in post titles.
        category_slug: Optional category slug (see list_blog_categories).
        page: Page number (default 1).
        per_page: Posts per page (default 9).
    """
    params = {"page": page, "per_page": per_page}
    if search:
        params["search"] = search
    if category_slug:
        params["category"] = category_slug
    try:
        data = _get("/blog/posts", params) or {}
    except httpx.HTTPError as e:
        return {"error": f"Blog API unavailable: {e}"}
    posts = [
        {
            "title": p.get("title"),
            "slug": p.get("slug"),
            "excerpt": p.get("excerpt"),
            "category": p.get("category"),
            "published_date": p.get("published_date"),
            "url": f"{SITE}/blog/{p.get('slug')}",
        }
        for p in data.get("posts") or []
    ]
    return {"total": data.get("total"), "pages": data.get("pages"), "page": page, "posts": posts}


@mcp.tool()
def get_blog_post(slug: str) -> dict:
    """Read a blog post by slug (content is truncated to about 6,000 characters).

    Args:
        slug: Post slug from list_blog_posts.
    """
    try:
        data = _get(f"/blog/posts/{slug}")
    except httpx.HTTPError as e:
        return {"error": f"Blog API unavailable: {e}"}
    post = (data or {}).get("post") if isinstance(data, dict) else None
    if not post:
        return {"error": f"No blog post with slug '{slug}'."}
    return {
        "title": post.get("title"),
        "slug": post.get("slug"),
        "category": post.get("category"),
        "tags": [t.get("name") for t in post.get("tags") or []],
        "published_date": post.get("published_date"),
        "excerpt": post.get("excerpt"),
        "content": _trim(post.get("content"), MAX_POST_CHARS),
        "url": f"{SITE}/blog/{post.get('slug')}",
    }


@mcp.tool()
def list_blog_categories() -> dict:
    """List blog categories (id, name, slug)."""
    try:
        data = _get("/blog/categories") or {}
    except httpx.HTTPError as e:
        return {"error": f"Blog API unavailable: {e}"}
    return {"categories": data.get("categories") or []}


# ---------- people & proof ----------
# Shown on the website's home page. Used only while the team database table is empty.
FACULTY_FALLBACK = [
    {"name": "Shovan Mishra", "designation": "Sr. UI/UX Faculty", "experience": "15+ years of production & teaching experience"},
    {"name": "Vikash Rout", "designation": "Sr. Photography Faculty", "experience": "10+ years"},
    {"name": "Ruchismita Jena", "designation": "Multimedia Faculty", "experience": "8+ years of teaching experience"},
    {"name": "Manjit Tripathy", "designation": "Sr. Motion Graphics Faculty", "experience": "12+ years of industry experience"},
    {"name": "Gayatri Sutar", "designation": "Sr. Multimedia Faculty", "experience": "7+ years of teaching experience"},
    {"name": "Nirupama Sutar", "designation": "Visual Art Faculty", "experience": "6+ years of teaching experience"},
    {"name": "Kundalata Tripathy", "designation": "Academic Head", "experience": "15+ years of teaching experience"},
    {"name": "Soumya Panda", "designation": "Sr. 3D Animation Faculty", "experience": "12+ years of teaching experience"},
    {"name": "Bibhu Prashad Das", "designation": "Sr. Multimedia Faculty", "experience": "17+ years of teaching & production experience"},
]


@mcp.tool()
def list_faculty() -> dict:
    """List Prism Media faculty and team members (name, designation, role, bio). Contact details are not exposed."""
    try:
        data = _get("/team/members") or {}
    except httpx.HTTPError as e:
        return {"error": f"Team API unavailable: {e}"}
    if not data.get("data"):
        return {
            "count": len(FACULTY_FALLBACK),
            "source": "prismmedia.org home page (the team database is empty)",
            "members": FACULTY_FALLBACK,
        }
    members = [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "designation": m.get("designation"),
            "role": m.get("role"),
            "bio": _trim(m.get("bio"), 400),
            "is_featured": bool(m.get("is_featured")),
        }
        for m in data.get("data") or []
    ]
    return {"count": len(members), "members": members}


@mcp.tool()
def get_faculty_member(member_id: int) -> dict:
    """Get a faculty/team member's profile: qualifications, experience, specializations, achievements.

    Args:
        member_id: Member id from list_faculty.
    """
    try:
        data = _get(f"/team/members/{member_id}")
    except httpx.HTTPError as e:
        return {"error": f"Team API unavailable: {e}"}
    m = (data or {}).get("data") if isinstance(data, dict) else None
    if not m or not m.get("is_active", True):
        return {"error": f"No team member with id {member_id}."}
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "designation": m.get("designation"),
        "role": m.get("role"),
        "bio": m.get("bio"),
        "qualifications": _maybe_json(m.get("qualifications")),
        "experience": _maybe_json(m.get("experience")),
        "specializations": _maybe_json(m.get("specializations")),
        "achievements": _maybe_json(m.get("achievements")),
    }


@mcp.tool()
def get_testimonials(limit: int = 10, course: str = "") -> dict:
    """Get approved student testimonials (name, where they work now, rating, text).

    Args:
        limit: Max testimonials (default 10).
        course: Optional text to match against the testimonial's course name or the student's role/company.
    """
    try:
        data = _get("/content/testimonials", {"per_page": 50}) or {}
    except httpx.HTTPError as e:
        return {"error": f"Testimonials API unavailable: {e}"}
    items = data.get("items") or []
    if course:
        needle = course.lower()
        items = [
            t for t in items
            if needle in " ".join(str(t.get(k) or "") for k in ("course_name", "author_designation", "company")).lower()
        ]
    out = [
        {
            "name": t.get("author_name") or t.get("title"),
            "designation": t.get("author_designation"),
            "company": t.get("company"),
            "course": t.get("course_name"),
            "rating": t.get("rating"),
            "testimonial": _trim(t.get("content"), 600),
            "video_url": t.get("video_url") if t.get("is_video") else None,
        }
        for t in items[:limit]
    ]
    return {"count": len(out), "total_available": data.get("total"), "testimonials": out}


@mcp.tool()
def get_institute_info(topic: str = "") -> dict:
    """Get Prism Media's contact details, address, affiliations, placement stats and key page links.

    Args:
        topic: Optional: "contact", "affiliations", "highlights" or "pages" to return only that part.
    """
    info = dict(INSTITUTE)
    info["pages"] = {k: f"{SITE}{v}" for k, v in INSTITUTE["pages"].items()}
    topic = topic.lower().strip()
    if topic == "contact":
        keys = ("address", "phone", "toll_free", "email", "whatsapp", "social")
    elif topic in ("affiliations", "highlights", "pages"):
        keys = (topic,)
    else:
        return info
    return {k: info[k] for k in keys}


# ======================================================================
# SEO tools: audit pages, structured data, sitemap, internal links, content gaps.
# They only read public pages / the public API and never change the site.
# ======================================================================
_HOST = urlparse(SITE).hostname or "prismmedia.org"
ALLOWED_AUDIT_HOSTS = {_HOST, f"www.{_HOST}"}
SEVERITY_COST = {"critical": 25, "error": 15, "warning": 8, "info": 3}
GENERIC_WORDS = {"diploma", "certification", "professional", "specialization", "program", "bachelor", "degree", "ug", "bsc"}
ORG_ID = f"{SITE}/#organization"
ORGANIZATION = {
    "@context": "https://schema.org",
    "@type": "EducationalOrganization",
    "@id": ORG_ID,
    "name": "Prism Media",
    "alternateName": "Prism Media Animation Institute Bhubaneswar",
    "url": SITE,
    "logo": f"{SITE}/assets/content/Logo.png",
    "description": "Creative education institute for Animation, VFX, Graphic Design, UI/UX, Digital Marketing and AI in Bhubaneswar, Odisha.",
    "foundingDate": "2014",
    "telephone": "+91-94371-52172",
    "email": "infoprismmediaodisha@gmail.com",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "Plot No.20, Acharya Vihar Square, Near Acharya Vihar Bus Stop",
        "addressLocality": "Bhubaneswar",
        "addressRegion": "Odisha",
        "postalCode": "751022",
        "addressCountry": "IN",
    },
    "areaServed": "Bhubaneswar, Odisha",
    "sameAs": list(INSTITUTE["social"].values()),
}
ORG_REF = {"@type": "EducationalOrganization", "@id": ORG_ID, "name": "Prism Media", "url": SITE}
STATIC_PAGES = ["/", "/about-us", "/blog", "/gallery", "/careers", "/contact-us", "/talenthub",
                "/student-testimonials", "/students-certificates", "/game"]


def _all_posts() -> list:
    posts, page = [], 1
    while page <= 60:
        data = _get("/blog/posts", {"page": page, "per_page": 50}) or {}
        posts.extend(data.get("posts") or [])
        if page >= (data.get("pages") or 1):
            break
        page += 1
    return posts


def _abs(url):
    if not url:
        return None
    return url if url.startswith("http") else f"{SITE}{url if url.startswith('/') else '/' + url}"


def _iso_duration(text):
    """'18 Months' -> 'P18M', '1 Year 6 Months' -> 'P1Y6M'."""
    units = {"year": "Y", "month": "M", "week": "W", "day": "D"}
    found = {}
    for n, unit in re.findall(r"(\d+)\s*(year|month|week|day)s?", (text or "").lower()):
        found[units[unit]] = n
    return ("P" + "".join(f"{found[u]}{u}" for u in "YMWD" if u in found)) if found else None


def _fetch_page(url: str):
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "PrismMediaSEO/1.0"}) as client:
        r = client.get(url)
        return r.status_code, str(r.url), r.text


def _norm_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/").lower()


def _audit_html(final_url: str, status: int, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    issues = []

    def add(sev, check, message, fix):
        issues.append({"severity": sev, "check": check, "message": message, "fix": fix})

    if status != 200:
        add("critical", "status", f"Page returned HTTP {status}.", "Make sure the URL returns 200 for crawlers.")

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    if not title:
        add("error", "title", "No <title> tag.", "Add a unique title of 30-60 characters with the main keyword.")
    elif len(title) < 30 or len(title) > 60:
        add("warning", "title", f"Title is {len(title)} characters.", "Aim for 30-60 so Google doesn't truncate it.")

    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (md.get("content") or "").strip() if md else ""
    if not desc:
        add("error", "meta_description", "No meta description.", "Add a unique 70-160 character description that invites the click.")
    elif len(desc) < 70 or len(desc) > 160:
        add("warning", "meta_description", f"Meta description is {len(desc)} characters.", "Aim for 70-160 characters.")

    canon = soup.find("link", rel="canonical")
    canon_href = (canon.get("href") or "").strip() if canon else ""
    if not canon_href:
        add("warning", "canonical", "No canonical link.", "Add <link rel=\"canonical\"> pointing to the preferred URL.")
    elif _norm_url(canon_href) != _norm_url(final_url):
        add("warning", "canonical", f"Canonical ({canon_href}) differs from the page URL.", "Check that this is intentional.")

    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    if robots and "noindex" in (robots.get("content") or "").lower():
        add("critical", "noindex", "Page has a noindex robots tag.", "Remove noindex if the page should appear in Google.")

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    if not h1s:
        add("error", "h1", "No <h1> heading.", "Add one H1 that describes the page.")
    elif len(h1s) > 1:
        add("warning", "h1", f"{len(h1s)} H1 headings.", "Use a single H1 and H2/H3 for sections.")

    if not soup.find("meta", attrs={"name": "viewport"}):
        add("warning", "viewport", "No viewport meta tag.", "Add width=device-width for mobile-friendliness.")
    if not (soup.html and soup.html.get("lang")):
        add("info", "lang", "No lang attribute on <html>.", "Add lang=\"en\".")

    imgs = soup.find_all("img")
    no_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
    if no_alt:
        add("warning", "image_alt", f"{len(no_alt)} of {len(imgs)} images have no alt text.", "Add short descriptive alt text.")

    ld_types = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            add("error", "structured_data", "A JSON-LD block is not valid JSON.", "Fix the JSON syntax.")
            continue
        for node in (data if isinstance(data, list) else [data]):
            nodes = node.get("@graph", [node]) if isinstance(node, dict) else []
            ld_types += [str(n.get("@type")) for n in nodes if isinstance(n, dict)]
    if not ld_types:
        add("warning", "structured_data", "No structured data (JSON-LD) found.", "Add Course/Article/Organization schema (see get_course_schema).")

    missing_og = [p for p in ("og:title", "og:description", "og:image") if not soup.find("meta", property=p)]
    if missing_og:
        add("info", "open_graph", f"Missing Open Graph tags: {', '.join(missing_og)}.", "Add them for better link previews.")

    body = soup.body or soup
    for t in body(["script", "style", "noscript"]):
        t.decompose()
    words = len(body.get_text(" ", strip=True).split())
    root = soup.find(id="root")
    if words < 150:
        spa = root is not None and len(root.get_text(strip=True)) < 50
        add(
            "critical" if spa else "warning",
            "content",
            f"Only {words} words in the HTML a crawler downloads"
            + (" (empty React root: content is added by JavaScript)." if spa else "."),
            "Prerender or server-render this page so Google and AI crawlers see the content without running JavaScript.",
        )

    host = (urlparse(final_url).hostname or "").lower()
    internal = [a["href"] for a in soup.find_all("a", href=True) if a["href"].startswith("/") or host in a["href"]]
    if len(internal) < 3:
        add("warning", "internal_links", f"Only {len(internal)} internal links in the HTML.", "Link to related courses and posts with descriptive anchor text.")

    score = max(0, 100 - sum(SEVERITY_COST[i["severity"]] for i in issues))
    return {
        "url": final_url,
        "status": status,
        "score": score,
        "metrics": {"title": title, "title_length": len(title), "meta_description_length": len(desc), "h1": h1s,
                    "word_count": words, "images": len(imgs), "images_missing_alt": len(no_alt),
                    "structured_data_types": ld_types, "internal_links": len(internal)},
        "issues": sorted(issues, key=lambda i: list(SEVERITY_COST).index(i["severity"])),
        "note": "Checked the raw HTML a crawler downloads; JavaScript is not executed.",
    }


@mcp.tool()
def seo_audit_page(url: str = "") -> dict:
    """Audit one prismmedia.org page for on-page SEO: title, description, headings, canonical, structured data,
    image alt text, internal links, and whether crawlers can see the content without JavaScript.

    Args:
        url: Full page URL on prismmedia.org. Defaults to the home page.
    """
    url = url or f"{SITE}/"
    u = urlparse(url)
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in ALLOWED_AUDIT_HOSTS:
        return {"error": f"Only pages on {', '.join(sorted(ALLOWED_AUDIT_HOSTS))} can be audited."}
    try:
        status, final_url, html = _fetch_page(url)
    except httpx.HTTPError as e:
        return {"error": f"Could not fetch {url}: {e}"}
    return _audit_html(final_url, status, html)


def _sitemap_entries() -> list:
    entries = {f"{SITE}{p}": None for p in STATIC_PAGES}
    for c in _all_courses():
        entries[_course_url(c)] = None
    for p in _all_posts():
        if p.get("slug"):
            entries[f"{SITE}/blog/{p['slug']}"] = (p.get("published_date") or "")[:10] or None
    return list(entries.items())


@mcp.tool()
def seo_audit_site(limit: int = 10) -> dict:
    """Audit several pages from the sitemap (home, main pages, courses, blog) and summarise the most common problems.

    Args:
        limit: How many pages to audit (default 10, max 30). Pages are taken in sitemap order.
    """
    limit = max(1, min(limit, 30))
    try:
        urls = [u for u, _ in _sitemap_entries()][:limit]
    except httpx.HTTPError as e:
        return {"error": f"Prism Media API unavailable: {e}"}
    results = []
    for u in urls:
        try:
            status, final_url, html = _fetch_page(u)
            results.append(_audit_html(final_url, status, html))
        except httpx.HTTPError as e:
            results.append({"url": u, "error": str(e), "score": 0, "issues": []})
    results.sort(key=lambda r: r["score"])
    by_check: dict = {}
    for r in results:
        for i in r["issues"]:
            by_check[i["check"]] = by_check.get(i["check"], 0) + 1
    return {
        "pages_audited": len(results),
        "average_score": round(sum(r["score"] for r in results) / max(len(results), 1)),
        "most_common_issues": dict(sorted(by_check.items(), key=lambda kv: -kv[1])),
        "pages_worst_first": [
            {"url": r["url"], "score": r["score"], "error": r.get("error"),
             "issues": [f"{i['severity']}: {i['message']}" for i in r["issues"]]}
            for r in results
        ],
    }


@mcp.tool()
def get_organization_schema() -> dict:
    """JSON-LD (EducationalOrganization) for Prism Media. Paste into the site's <head> inside <script type="application/ld+json">."""
    return ORGANIZATION


@mcp.tool()
def get_course_schema(slug: str, include_price: bool = False) -> dict:
    """JSON-LD for a course page (Course, plus FAQPage when the course has FAQs).

    Args:
        slug: Course slug from search_courses.
        include_price: Add an Offer with the fee. Only enable if the page shows the same price.
    """
    try:
        c = next((x for x in _all_courses() if x.get("slug") == slug), None)
    except httpx.HTTPError as e:
        return {"error": f"Course API unavailable: {e}"}
    if not c:
        return {"error": f"No active course with slug '{slug}'."}
    course = {
        "@type": "Course",
        "name": c.get("name"),
        "description": c.get("short_description") or c.get("description") or c.get("name"),
        "url": _course_url(c),
        "inLanguage": c.get("language") or "en",
        "provider": ORG_REF,
        "hasCourseInstance": {"@type": "CourseInstance", "courseMode": "onsite", "location": ORGANIZATION["address"]},
    }
    if c.get("level"):
        course["educationalLevel"] = str(c["level"]).capitalize()
    if c.get("image_url"):
        course["image"] = _abs(c["image_url"])
    iso = _iso_duration(c.get("duration"))
    if iso:
        course["timeRequired"] = iso
        course["hasCourseInstance"]["courseWorkload"] = iso
    price = c.get("discount_price") or c.get("price")
    if include_price and price and price > 0:
        course["offers"] = {"@type": "Offer", "price": price, "priceCurrency": "INR", "category": "Paid"}
    graph = [course]
    syllabus = _maybe_json(c.get("syllabus"))
    faqs = syllabus.get("faqs") if isinstance(syllabus, dict) else None
    faqs = [f for f in (faqs or []) if f.get("question") and f.get("answer")]
    if faqs:
        graph.append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["question"], "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
            for f in faqs]})
    return {"@context": "https://schema.org", "@graph": graph}


@mcp.tool()
def get_blog_schema(slug: str) -> dict:
    """JSON-LD (BlogPosting) for a blog post.

    Args:
        slug: Post slug from list_blog_posts.
    """
    try:
        p = next((x for x in _all_posts() if x.get("slug") == slug), None)
    except httpx.HTTPError as e:
        return {"error": f"Blog API unavailable: {e}"}
    if not p:
        return {"error": f"No blog post with slug '{slug}'."}
    url = f"{SITE}/blog/{slug}"
    data = {
        "@context": "https://schema.org", "@type": "BlogPosting",
        "headline": (p.get("title") or "")[:110], "description": p.get("excerpt") or "",
        "mainEntityOfPage": {"@type": "WebPage", "@id": url}, "url": url,
        "author": {"@type": "Organization", "name": "Prism Media", "url": SITE}, "publisher": ORG_REF,
    }
    if p.get("published_date"):
        data["datePublished"] = data["dateModified"] = p["published_date"]
    if p.get("featured_image"):
        data["image"] = _abs(p["featured_image"])
    if p.get("category"):
        data["articleSection"] = p["category"]
    return data


@mcp.tool()
def generate_sitemap() -> dict:
    """Generate sitemap.xml content for prismmedia.org: main pages, every active course and every blog post."""
    try:
        entries = _sitemap_entries()
    except httpx.HTTPError as e:
        return {"error": f"Prism Media API unavailable: {e}"}
    rows = []
    for loc, lastmod in entries:
        rows.append("  <url>\n    <loc>" + escape(loc) + "</loc>\n"
                    + (f"    <lastmod>{lastmod}</lastmod>\n" if lastmod else "") + "  </url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(rows) + "\n</urlset>\n")
    return {"url_count": len(entries), "sitemap_xml": xml}


def _name_tokens(course: dict) -> set:
    return {t for t in _tokens(course.get("name") or "") if t not in GENERIC_WORDS}


@mcp.tool()
def suggest_internal_links(limit: int = 30) -> dict:
    """Suggest links from blog posts to the courses they are about (internal links help rankings and sales).
    Based on matching course names against post titles and excerpts. Check the post doesn't already link to the course.

    Args:
        limit: Max suggestions (default 30).
    """
    try:
        courses, posts = _all_courses(), _all_posts()
    except httpx.HTTPError as e:
        return {"error": f"Prism Media API unavailable: {e}"}
    suggestions = []
    for p in posts:
        ptoks = set(_tokens(f"{p.get('title') or ''} {p.get('excerpt') or ''}"))
        scored = []
        for c in courses:
            hits = _name_tokens(c) & ptoks
            if hits:
                scored.append((len(hits), c, sorted(hits)))
        scored.sort(key=lambda x: -x[0])
        for score, c, hits in scored[:2]:
            suggestions.append({
                "score": score,
                "post": p.get("title"),
                "post_url": f"{SITE}/blog/{p.get('slug')}",
                "link_to": _course_url(c),
                "suggested_anchor_text": c.get("name"),
                "matched_terms": hits,
            })
    suggestions.sort(key=lambda s: -s["score"])
    return {"count": len(suggestions[:limit]), "suggestions": suggestions[:limit]}


@mcp.tool()
def find_content_gaps(limit: int = 10) -> dict:
    """Show which courses have the least blog support, with blog topic ideas for each.
    Ideas are templates to adapt; they are not search-volume data, so check them in Google Search Console or a keyword tool.

    Args:
        limit: How many courses to list (default 10), least covered first.
    """
    try:
        courses, posts = _all_courses(), _all_posts()
    except httpx.HTTPError as e:
        return {"error": f"Prism Media API unavailable: {e}"}
    post_tokens = [set(_tokens(f"{p.get('title') or ''} {p.get('excerpt') or ''}")) for p in posts]
    rows = []
    for c in courses:
        nt = _name_tokens(c)
        count = sum(1 for pt in post_tokens if nt and nt & pt)
        name = c.get("name")
        rows.append({
            "course": name,
            "url": _course_url(c),
            "related_posts": count,
            "topic_ideas": [
                f"{name} in Bhubaneswar: syllabus, fees, eligibility and career options",
                f"Career options and salaries after {name}",
                f"Is {name} worth it? Who should join and what you will learn",
            ],
        })
    rows.sort(key=lambda r: r["related_posts"])
    return {"posts_checked": len(posts), "courses": rows[:limit]}


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))