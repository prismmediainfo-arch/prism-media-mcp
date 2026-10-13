# Prism Media MCP server

Read-only [MCP](https://modelcontextprotocol.io) server for Prism Media (prismmedia.org).
It lets an AI assistant answer questions about Prism Media's courses, curriculum, blog, faculty,
testimonials and contact details using live data from the Prism Media API.
It never writes to the API.

## Tools

| Tool | What it does |
|---|---|
| `search_courses` | Find courses by topic, skill or job role |
| `get_course` | Full details: overview, duration, what you learn, syllabus, curriculum, fees |
| `list_course_categories` | Course categories |
| `recommend_path` | Suggested courses (beginner to advanced) for a goal |
| `list_blog_posts`, `get_blog_post`, `list_blog_categories` | Blog |
| `list_faculty`, `get_faculty_member` | Faculty (no contact details) |
| `get_testimonials` | Approved student testimonials |
| `get_institute_info` | Address, contact, affiliations, placement stats, page links |

### SEO tools (help find and fix ranking problems)

| Tool | What it does |
|---|---|
| `seo_audit_page` | Audits one prismmedia.org page: title, meta description, H1, canonical, alt text, structured data, empty-React-page check; score 0-100 with fixes |
| `seo_audit_site` | Audits the home page plus key course pages and ranks the worst first |
| `get_organization_schema` | Ready-to-paste JSON-LD for the institute |
| `get_course_schema` | JSON-LD for a course (price only if `include_price=true`) |
| `get_blog_schema` | JSON-LD BlogPosting for a post |
| `generate_sitemap` | sitemap.xml built from live courses and blog posts |
| `suggest_internal_links` | Which blog posts should link to which course pages |
| `find_content_gaps` | Courses with the fewest blog posts, with post ideas |

These tools suggest and check; they do not change the website. Topic ideas are not search-volume data.

## Run locally

    pip install -r requirements.txt
    set PRISM_API_URL=https://prism-backend-2m0c.onrender.com/api/v1
    python server.py

PowerShell: `$env:PRISM_API_URL = "https://prism-backend-2m0c.onrender.com/api/v1"`.
Default transport is stdio. For HTTP (what Claude on the web and other remote clients use):

    set MCP_TRANSPORT=streamable-http
    python server.py        # serves http://localhost:8000/mcp

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PRISM_API_URL` | `https://prismmedia.org/api/v1` | Prism Media API base |
| `PRISM_SITE_URL` | `https://prismmedia.org` | Used for links |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `$PORT` or `8000` | HTTP bind address |

## Deploy on Render (new, separate service)

1. Push this folder to a new GitHub repo.
2. Render -> New -> Blueprint (uses `render.yaml`), or New -> Web Service with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `MCP_TRANSPORT=streamable-http python server.py`
3. Set `PRISM_API_URL` to your API base.
4. Your MCP URL is `https://<service>.onrender.com/mcp`.
5. In Claude: Settings -> Connectors -> Add custom connector, paste that URL.

The free Render plan sleeps when idle, so the first request after a pause can take about a minute.
The endpoint is public and read-only, and only serves data that is already public on the website.