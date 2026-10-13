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

import httpx
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


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
