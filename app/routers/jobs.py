# app/routers/jobs.py
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from geopy.distance import geodesic
from sqlalchemy import func
from app.database import get_db
from app.models import User, Skill, WorkerProfile, Job, JobDistanceCache
from app.settings import settings
import html as html_module  # for escaping badge labels
from app.security.tokens import encode_worker_link
import math
import requests
from app.security.auth import get_current_user


router = APIRouter(tags=["jobs"])


def distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    to_rad = lambda x: x * math.pi / 180
    dlat = to_rad(lat2 - lat1)
    dlon = to_rad(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Helper: determine if a skill category string represents remote/work-from-home
def _is_remote_category(cat: str) -> bool:
    if not cat:
        return False

    c = cat.strip().lower()

    return c in (
        "sahayi from home",
        "sahayi-from-home",
        "sahayi_from_home",
        "wfh",
        "work from home",
        "work-from-home",
        "remote",
    )


# ------------------ GET form ------------------
@router.get("/provide_job", response_class=HTMLResponse)
@router.get("/provide_job/", response_class=HTMLResponse)  # allow trailing slash
def provide_job_form(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> HTMLResponse:
    skills = db.query(Skill).all()
    skill_names = [(s.name or "").strip().lower() for s in skills]

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Search for Sahayi</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{
                font-family: Arial, sans-serif;
                padding: 20px;
                margin: 0;
                background-color: #f2f2f2;
            }}
            .form-container {{
                max-width: 600px;
                margin: auto;
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                position: relative;
            }}
            label {{
                font-weight: bold;
                margin-bottom: 5px;
                display: block;
            }}
            input[type="text"] {{
                width: 100%;
                padding: 10px;
                margin-top: 5px;
                border: 1px solid #ccc;
                border-radius: 5px;
                box-sizing: border-box;
            }}
            input[type="submit"] {{
                background-color: #007bff;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                width: 100%;
                margin-top: 10px;
            }}
            input[type="submit"]:hover {{
                background-color: #0056b3;
            }}
            .suggestions {{
                position: absolute;
                width: 100%;
                background: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                max-height: 200px;
                overflow-y: auto;
                display: none;
                z-index: 1000;
            }}
            .suggestions div {{
                padding: 10px;
                cursor: pointer;
            }}
            .suggestions div:hover {{
                background-color: #e9ecef;
            }}
        </style>
    </head>
    <body>
        <div class="form-container">
            <h2 class="text-center">🔍 Search for Sahayi</h2>
            <form method="POST" autocomplete="off">
                <label for="job_type">What help do you need?</label>
                <input type="text" name="job_type" id="job_type" placeholder="e.g., plumber, electrician" required>
                <div id="suggestions" class="suggestions"></div>
                <input type="submit" value="Search Workers Near You">
            </form>
        </div>

        <script>
            const skills = {skill_names!r};
            const input = document.getElementById('job_type');
            const box = document.getElementById('suggestions');

            input.addEventListener('input', () => {{
                const query = (input.value || '').toLowerCase().trim();
                box.innerHTML = '';
                if (!query) {{ box.style.display = 'none'; return; }}

                const filtered = skills.filter(s => s.includes(query)).slice(0, 8);
                if (!filtered.length) {{ box.style.display = 'none'; return; }}

                filtered.forEach(skill => {{
                    const div = document.createElement('div');
                    div.textContent = skill;
                    div.onclick = () => {{
                        input.value = skill;
                        box.style.display = 'none';
                    }};
                    box.appendChild(div);
                }});
                box.style.display = 'block';
            }});

            document.addEventListener('click', (e) => {{
                if (!box.contains(e.target) && e.target !== input) {{
                    box.style.display = 'none';
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.post("/distance_cached")
def distance_cached(
        payload: dict,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    required = ["job_id", "skill_id", "user_lat", "user_lon", "worker_lat", "worker_lon"]
    for k in required:
        if k not in payload:
            return {"ok": False, "message": f"Missing field: {k}"}

    job_id = int(payload["job_id"])
    skill_id = int(payload["skill_id"])

    user_lat = float(payload["user_lat"])
    user_lon = float(payload["user_lon"])

    worker_lat = float(payload["worker_lat"])
    worker_lon = float(payload["worker_lon"])

    MAPBOX_TOKEN = settings.MAPBOX_ACCESS_TOKEN
    if not MAPBOX_TOKEN:
        return {"ok": False, "message": "Mapbox token missing"}

    # ✅ Check existing cache
    cache = (
        db.query(JobDistanceCache)
        .filter(JobDistanceCache.job_id == job_id, JobDistanceCache.skill_id == skill_id)
        .first()
    )

    # ✅ If cache exists → validate movement threshold (1 km)
    if cache:
        moved_worker = distance_meters(cache.worker_lat, cache.worker_lon, worker_lat, worker_lon)
        moved_user = distance_meters(cache.user_lat, cache.user_lon, user_lat, user_lon)

        if moved_worker < 1000 and moved_user < 1000:
            return {
                "ok": True,
                "cached": True,
                "distance_km": cache.distance_km,
                "duration_min": cache.duration_min
            }

    # ✅ Otherwise recalc using Mapbox Directions API
    url = f"https://api.mapbox.com/directions/v5/mapbox/driving/{user_lon},{user_lat};{worker_lon},{worker_lat}"
    params = {"access_token": MAPBOX_TOKEN, "overview": "false"}

    r = requests.get(url, params=params, timeout=10)
    j = r.json()

    if j.get("code") != "Ok" or not j.get("routes"):
        return {"ok": False, "message": "Mapbox failed", "raw": j}

    dist_km = round(j["routes"][0]["distance"] / 1000, 2)
    dur_min = round(j["routes"][0]["duration"] / 60, 1)

    # ✅ Save / update cache
    if not cache:
        cache = JobDistanceCache(
            job_id=job_id,
            skill_id=skill_id,
            worker_lat=worker_lat,
            worker_lon=worker_lon,
            user_lat=user_lat,
            user_lon=user_lon,
            distance_km=dist_km,
            duration_min=dur_min,
        )
        db.add(cache)
    else:
        cache.worker_lat = worker_lat
        cache.worker_lon = worker_lon
        cache.user_lat = user_lat
        cache.user_lon = user_lon
        cache.distance_km = dist_km
        cache.duration_min = dur_min

    db.commit()

    return {
        "ok": True,
        "cached": False,
        "distance_km": dist_km,
        "duration_min": dur_min
    }


# ------------------ POST search ------------------
@router.post("/provide_job", response_class=HTMLResponse)
@router.post("/provide_job/", response_class=HTMLResponse)  # allow trailing slash
def provide_job_submit(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> HTMLResponse:
    # Safer: read form via request directly
    import anyio
    async def _read_form():
        from starlette.datastructures import FormData
        return await request.form()

    form_data = anyio.from_thread.run(_read_form)  # run sync context

    job_type_raw = (form_data.get("job_type") or "").strip().lower()
    if not job_type_raw:
        return HTMLResponse("<p>Error: job_type is required.</p>", status_code=400)

    user_lat = current_user.latitude
    user_lon = current_user.longitude
    if user_lat is None or user_lon is None:
        return HTMLResponse("<p>Error: Please enable location so we can find nearby workers.</p>", status_code=400)

    # Create a Job
    job = Job(
        title=job_type_raw.title(),
        description=f"You are seeking help for: {job_type_raw.title()}",
        user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Match workers by skill keywords (collect matching Skill objects)
    job_keywords = set(job_type_raw.split())
    skills = db.query(Skill).all()

    matching_skills = []
    for skill in skills:
        skill_name = (skill.name or "").strip().lower()
        if not skill_name:
            continue
        if job_keywords & set(skill_name.split()):
            matching_skills.append(skill)

    # If no skills matched, quick response
    if not matching_skills:
        return HTMLResponse(
            "<div class='p-4 text-center'>😞 No matching workers found within 105 km radius.</div>", status_code=200
        )

    # Inspect categories on matching skills to discover if both remote and non-remote exist
    has_remote = False
    has_non_remote = False
    for s in matching_skills:
        cat = (s.category or "").strip()
        if _is_remote_category(cat):
            has_remote = True
        else:
            has_non_remote = True

    # If both modes exist and user hasn't chosen, show modern interactive choice page
    chosen_mode = (form_data.get("mode") or "").strip().lower()  # expected: "wfh" or "onsite"
    if not chosen_mode and has_remote and has_non_remote:
        # counts for UI
        remote_count = sum(1 for s in matching_skills if _is_remote_category((s.category or "").strip()))
        onsite_count = len(matching_skills) - remote_count
        # safe escaped values
        skill_clean = html_module.escape(job_type_raw.title())
        skill_clean_short = html_module.escape(skill_clean[:2])

        # Template uses simple placeholders to avoid accidental format() parsing
        choice_template = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8"/>
          <meta name="viewport" content="width=device-width,initial-scale=1"/>
          <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
          <title>Choose Work Mode</title>
          <style>
            :root {
              --accent: #2563eb;
              --accent-2: #06b6d4;
              --card-bg: #ffffff;
              --muted: #6b7280;
            }
            body {
              background: linear-gradient(180deg,#f3f7fb 0%, #ffffff 60%);
              font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
              margin:0;
              -webkit-font-smoothing:antialiased;
            }
            .choice-shell {
              min-height: 100vh;
              display: flex;
              align-items: center;
              justify-content: center;
              padding: 48px 20px;
            }
            .choice-card {
              max-width: 920px;
              width: 100%;
              background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(250,250,255,0.9));
              border-radius: 16px;
              box-shadow: 0 20px 60px rgba(2,6,23,0.12);
              padding: 28px;
            }
            .choice-head {
              display: flex;
              gap: 18px;
              align-items: center;
            }
            .badge-skill {
              width:64px; height:64px; border-radius:12px;
              display:flex; align-items:center; justify-content:center;
              font-weight:700; color:white; background: linear-gradient(135deg,var(--accent), var(--accent-2));
              box-shadow: 0 8px 24px rgba(37,99,235,0.18);
              font-size:20px;
            }
            h1.title { margin:0; font-size:20px; }
            p.lead { margin:0; color:var(--muted); margin-top:6px; }

            .options {
              display:grid;
              grid-template-columns: 1fr 1fr;
              gap:18px;
              margin-top:20px;
            }

            .mode-card {
              background:var(--card-bg);
              border-radius:12px;
              padding:18px;
              border:1px solid rgba(15,23,42,0.06);
              transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
              cursor:pointer;
              display:flex;
              gap:12px;
              align-items:center;
            }
            .mode-card:hover {
              transform: translateY(-6px);
              box-shadow: 0 18px 40px rgba(2,6,23,0.06);
              border-color: rgba(37,99,235,0.12);
            }
            .mode-card.selected {
              border-color: rgba(37,99,235,0.22);
              box-shadow: 0 28px 60px rgba(37,99,235,0.12);
              transform: translateY(-10px) scale(1.01);
            }
            .mode-ico {
              width:56px;height:56px;border-radius:10px;
              display:flex;align-items:center;justify-content:center;
              font-size:22px;color:white;
              flex-shrink:0;
            }
            .ico-remote { background: linear-gradient(135deg,#06b6d4,#0ea5e9); }
            .ico-onsite { background: linear-gradient(135deg,#34d399,#10b981); }

            .mode-body { flex:1; }
            .mode-title { font-weight:600; margin:0; }
            .mode-sub { margin-top:6px; color:var(--muted); font-size:0.92rem; }
            .mode-count { font-weight:700; color:#111827; margin-top:8px; }

            .actions {
              display:flex;
              gap:12px;
              align-items:center;
              justify-content:center;
              margin-top:18px;
            }
            .btn-confirm {
              padding:10px 18px;
              border-radius:10px;
              font-weight:600;
              box-shadow: 0 10px 30px rgba(2,6,23,0.08);
              border:none;
            }
            .btn-secondary {
              background:transparent;border:1px solid rgba(2,6,23,0.06); color:var(--muted);
            }
            @media (max-width:700px) {
              .options { grid-template-columns: 1fr; }
            }
          </style>
        </head>
        <body>
          <div class="choice-shell">
            <div class="choice-card">
              <div class="choice-head">
                <div class="badge-skill">__SKILL_SHORT__</div>
                <div>
                  <h1 class="title">How should <strong>__SKILL__</strong> be performed?</h1>
                  <p class="lead">We found listings registered for both remote (Sahayi from Home) and on-site variants. Pick the type you need — you can change later.</p>
                </div>
              </div>

              <form id="modeForm" method="POST">
                <input type="hidden" name="job_type" value="__JOB_TYPE__">
                <input type="hidden" name="mode" id="modeInput" value="">
                <div class="options mt-3">
                  <div id="card-remote" class="mode-card" data-mode="wfh" role="button" tabindex="0" aria-pressed="false">
                    <div class="mode-ico ico-remote">💻</div>
                    <div class="mode-body">
                      <div class="mode-title">Sahayi from Home (Remote)</div>
                      <div class="mode-sub">Get help through video, phone or online tools. No site visit required.</div>
                      <div class="mode-count">__REMOTE_COUNT__ remote listing__REMOTE_PLURAL__</div>
                    </div>
                  </div>

                  <div id="card-onsite" class="mode-card" data-mode="onsite" role="button" tabindex="0" aria-pressed="false">
                    <div class="mode-ico ico-onsite">🔧</div>
                    <div class="mode-body">
                      <div class="mode-title">On-site (Worker visits your location)</div>
                      <div class="mode-sub">Worker will travel to your address to perform the job.</div>
                      <div class="mode-count">__ONSITE_COUNT__ on-site listing__ONSITE_PLURAL__</div>
                    </div>
                  </div>
                </div>

                <div class="actions">
                  <button type="button" id="confirmBtn" class="btn btn-primary btn-confirm" disabled>Choose a mode</button>
                  <a href="/provide_job" class="btn btn-link">← Start a new search</a>
                </div>
              </form>
            </div>
          </div>

          <script>
            (function(){
              const remoteCard = document.getElementById('card-remote');
              const onsiteCard = document.getElementById('card-onsite');
              const modeInput = document.getElementById('modeInput');
              const confirmBtn = document.getElementById('confirmBtn');
              let selected = null;

              function selectCard(card){
                [remoteCard, onsiteCard].forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                selected = card.getAttribute('data-mode');
                modeInput.value = selected;
                confirmBtn.disabled = false;
                confirmBtn.textContent = selected === 'wfh' ? 'Show remote results' : 'Show on-site results';
              }

              remoteCard.addEventListener('click', () => selectCard(remoteCard));
              onsiteCard.addEventListener('click', () => selectCard(onsiteCard));

              // allow Enter / Space to select when focused
              [remoteCard, onsiteCard].forEach(c => {
                c.addEventListener('keydown', (ev) => {
                  if(ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    selectCard(c);
                  }
                });
              });

              confirmBtn.addEventListener('click', function(){
                if(!selected) return;
                // submit the form
                document.getElementById('modeForm').submit();
              });

              // small flourish: preselect sensible option on mobile (onsite)
              if(window.innerWidth < 640) { selectCard(onsiteCard); }
            })();
          </script>
        </body>
        </html>
        """

        # replace the placeholders with safe values
        choice_html = (
            choice_template
            .replace("__JOB_TYPE__", html_module.escape(job_type_raw))
            .replace("__SKILL__", skill_clean)
            .replace("__SKILL_SHORT__", skill_clean_short)
            .replace("__REMOTE_COUNT__", str(remote_count))
            .replace("__ONSITE_COUNT__", str(onsite_count))
            .replace("__REMOTE_PLURAL__", "" if remote_count == 1 else "s")
            .replace("__ONSITE_PLURAL__", "" if onsite_count == 1 else "s")
        )

        return HTMLResponse(content=choice_html)

    # Normalize chosen_mode into filters
    want_remote_only = False
    want_onsite_only = False
    if chosen_mode:
        if chosen_mode in ("wfh", "remote"):
            want_remote_only = True
        elif chosen_mode in ("onsite", "on-site"):
            want_onsite_only = True

    # Now perform matching but filter skills by chosen/implicit mode.
    # Default behavior: when user provided location and no choice, prefer on-site,
    # but allow remote results when only remote listings exist.
    matched_workers: Dict[int, dict] = {}
    for skill in matching_skills:
        cat = (skill.category or "").strip()
        skill_is_remote = _is_remote_category(cat)

        if want_remote_only and not skill_is_remote:
            continue
        if want_onsite_only and skill_is_remote:
            continue

        if not chosen_mode:
            # Only apply the on-site bias when *both* remote and non-remote listings exist.
            # If all matches are remote, allow those remote matches even when location is present.
            if has_remote and has_non_remote and user_lat is not None and user_lon is not None and skill_is_remote:
                continue

        worker_user = db.get(User, skill.user_id)
        if not worker_user or worker_user.latitude is None or worker_user.longitude is None:
            continue

        profile = db.query(WorkerProfile).filter(
            WorkerProfile.user_id == worker_user.id
        ).first()

        # 🔒 MODERATION ENFORCEMENT (INSTAGRAM STYLE)
        if not profile:
            continue

        if profile.moderation_status in ("limited", "suspended", "banned"):
            continue

        if not profile.is_online or worker_user.busy:
            continue

        distance_km = geodesic(
            (user_lat, user_lon),
            (worker_user.latitude, worker_user.longitude),
        ).km

        if distance_km <= 105 and worker_user.id not in matched_workers:
            matched_workers[worker_user.id] = {
                "user": worker_user,
                "skill_id": skill.id,
                "skill_name": skill.name,
                "skill_category": skill.category or "",
                "rate": getattr(skill, "rate", None),
                "rate_type": getattr(skill, "rate_type", None),
                "distance": round(distance_km, 2),
            }

    if not matched_workers:
        return HTMLResponse(
            "<div class='p-4 text-center'>😞 No matching workers found for the selected mode within 105 km radius.</div>",
            status_code=200,
        )

    # Build keyed_by_skill matching format expected by _render_results_page (skill.id -> data)
    keyed_by_skill = {}
    for d in matched_workers.values():
        keyed_by_skill[d["skill_id"]] = {**d, "job_id": job.id}

    heading = f"Search results for: {job_type_raw.title()}"
    return _render_results_page(heading=heading, matched_workers=keyed_by_skill, user_lat=user_lat, user_lon=user_lon)


def _render_results_page(
        *, heading: str, matched_workers: Dict[int, dict], user_lat: float, user_lon: float
) -> HTMLResponse:
    from app.settings import settings

    matches_count = len(matched_workers)
    match_label = "match" if matches_count == 1 else "matches"

    # Build worker cards
    matched_list = ""
    for data in matched_workers.values():
        cat = (data.get("skill_category") or "").strip().lower()
        user = data["user"]
        job_id = data.get("job_id")

        token = encode_worker_link(
            worker_id=user.id,
            job_id=job_id,
            skill_id=data["skill_id"],
        )

        if _is_remote_category(cat):
            rate = "💬 Price to be confirmed"
        else:
            if data.get("rate") is not None and data.get("rate_type"):
                rate = f"₹{data['rate']} / {data['rate_type']}"
            else:
                rate = "N/A"

        user = data["user"]
        skill_name = (data.get("skill_name") or "").title()
        job_id = data.get("job_id", "")

        # Extract skill category for badge rendering
        cat = (data.get("skill_category") or "").strip()
        if _is_remote_category(cat):
            badge_html = '<span class="badge bg-info text-dark">Remote (Sahayi from Home)</span>'
        elif cat:
            # escape category to be safe
            badge_html = f'<span class="badge bg-success">On-site: {html_module.escape(cat)}</span>'
        else:
            badge_html = '<span class="badge bg-secondary">Mode not set</span>'

        # use a key per skill (unique per card)
        key = data["skill_id"]

        matched_list += f"""
        <div class="col-12 col-md-4">
          <article
            class="worker-card h-100"
            data-href="/worker/{token}"
          >
            <div class="card-top-row d-flex align-items-center mb-2">
              <div class="worker-avatar me-2">
                {user.name[:1].upper()}
              </div>
              <div class="flex-grow-1">
                <h2 class="worker-name mb-0">{user.name}</h2>
                <div class="worker-skill small text-muted">{skill_name}</div>
                <div style="margin-top:6px">{badge_html}</div>
              </div>
              <!-- ID now uses skill_id, not user.id -->
              <span class="pill-distance small text-muted" id="pill-distance-{key}">
                …
              </span>
            </div>

            <ul class="list-unstyled small mb-3">
              <li class="mb-1">
                <span class="me-1">💼</span>
                <strong>{rate}</strong>
              </li>
              <li>
                <span class="me-1">📍</span>
                Driving Distance:
                <!-- ID now uses skill_id, not user.id -->
                <span id="distance-{key}">Calculating...</span>
              </li>
            </ul>

            <div class="d-flex gap-2">
              <a
                href="/worker/{token}"
                class="btn btn-primary btn-sm flex-fill"
              >
                👤 View Profile
              </a>
              <button
                type="button"
                class="btn btn-outline-primary btn-sm flex-fill"
                onclick="startQuickCall('{token}')"
              >
                📞 Call
              </button>
            </div>
          </article>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Matching Workers</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      <script src="/static/js/theme.js" defer></script>
      <style>
        body {{
          min-height: 100vh;
          margin: 0;
          font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
          background:
            radial-gradient(circle at 0% 0%, #e0f2fe 0, #f5f3ff 40%, #f9fafb 75%, #eef2ff 100%);
          position: relative;
          overflow-x: hidden;
        }}
        body::before {{
          content: "";
          position: fixed;
          inset: -120px;
          background:
            radial-gradient(circle at 10% 20%, rgba(59,130,246,0.18) 0, transparent 40%),
            radial-gradient(circle at 80% 10%, rgba(16,185,129,0.18) 0, transparent 45%),
            radial-gradient(circle at 50% 90%, rgba(139,92,246,0.12) 0, transparent 45%);
          z-index: -1;
        }}
        .match-header {{
          background: linear-gradient(135deg, #0ea5e9, #2563eb);
          color: #fff;
          padding: 1.25rem 0 1rem;
          box-shadow: 0 6px 20px rgba(15, 23, 42, 0.35);
          border-bottom-left-radius: 22px;
          border-bottom-right-radius: 22px;
        }}
        .match-eyebrow {{
          text-transform: uppercase;
          letter-spacing: .12em;
          font-size: 0.7rem;
          opacity: 0.8;
        }}
        .match-title {{
          font-size: 1.2rem;
          font-weight: 600;
          display: flex;
          align-items: center;
          gap: .4rem;
        }}
        .match-subtitle {{
          font-size: 0.85rem;
          opacity: 0.92;
        }}
        .match-tags {{
          margin-top: .75rem;
        }}
        .match-tag {{
          display: inline-flex;
          align-items: center;
          gap: .35rem;
          padding: .25rem .7rem;
          border-radius: 999px;
          font-size: 0.78rem;
          background: rgba(255,255,255,0.9);
          border: 1px solid rgba(148,163,184,0.4);
          color: #1f2933;
          backdrop-filter: blur(6px);
          box-shadow: 0 4px 12px rgba(15,23,42,0.12);
        }}
        .results-shell {{
          max-width: 1080px;
        }}
        .worker-card {{
          background: rgba(255,255,255,0.96);
          border-radius: 18px;
          padding: 0.9rem 1rem 1rem;
          border: 1px solid #e2e8f0;
          box-shadow: 0 12px 30px rgba(15, 23, 42, 0.12);
          transition:
            transform 0.16s ease-out,
            box-shadow 0.16s ease-out,
            border-color 0.16s ease-out,
            background 0.16s ease-out;
          cursor: pointer;
          position: relative;
          overflow: hidden;
        }}
        .worker-card::before {{
          content: "";
          position: absolute;
          inset: 0;
          background: linear-gradient(135deg, rgba(59,130,246,0.09), rgba(59,130,246,0));
          opacity: 0;
          transition: opacity .18s ease-out;
        }}
        .worker-card:hover {{
          transform: translateY(-4px);
          box-shadow: 0 18px 38px rgba(15, 23, 42, 0.18);
          border-color: #3b82f6;
        }}
        .worker-card:hover::before {{
          opacity: 1;
        }}
        .card-top-row {{
          position: relative;
          z-index: 1;
        }}
        .worker-avatar {{
          width: 42px;
          height: 42px;
          border-radius: 999px;
          background: linear-gradient(135deg, #2563eb, #1d4ed8);
          display: flex;
          align-items: center;
          justify-content: center;
          color: #f9fafb;
          font-weight: 600;
          font-size: 1.15rem;
          box-shadow: 0 10px 24px rgba(37,99,235,0.45);
        }}
        .worker-name {{
          font-size: 0.98rem;
          font-weight: 600;
        }}
        .worker-skill {{
          font-size: 0.8rem;
        }}
        .pill-distance {{
          padding: 0.1rem .55rem;
          border-radius: 999px;
          border: 1px solid #e5e7eb;
          background: rgba(248,250,252,0.9);
        }}
        @media (max-width: 576px) {{
          .match-header {{
            padding-top: 1.05rem;
            padding-bottom: 0.85rem;
          }}
          .worker-card {{
            padding: 0.85rem 0.9rem 0.95rem;
            border-radius: 20px;
          }}
        }}
        
        /* =========================
           DARK MODE - RESULTS PAGE
           ========================= */
        
        html[data-theme="dark"] body {{
          background: radial-gradient(circle at top left, #020617, #020617 40%, #000814 100%) !important;
          color: #f8fafc !important;
        }}
        
        /* Worker cards */
        html[data-theme="dark"] .worker-card {{
          background: #0b1220 !important;
          border: 1px solid #1e293b !important;
          box-shadow: 0 12px 30px rgba(0,0,0,0.6) !important;
        }}
        
        /* Distance pill */
        html[data-theme="dark"] .pill-distance {{
          background: #020617 !important;
          border: 1px solid #1e293b !important;
          color: #e5e7eb !important;
        }}
        
        /* Header */
        html[data-theme="dark"] .match-header {{
          background: linear-gradient(135deg, #020617, #0b1220) !important;
        }}
        
        /* Tags */
        html[data-theme="dark"] .match-tag {{
          background: #020617 !important;
          border: 1px solid #1e293b !important;
          color: #f8fafc !important;
        }}
        
        /* Muted text */
        html[data-theme="dark"] .text-muted {{
          color: #cbd5e1 !important;
        }}
        
        /* Badges */
        html[data-theme="dark"] .badge {{
          background: #020617 !important;
          color: #ffffff !important;
          border: 1px solid #1e293b !important;
        }}

      </style>
    </head>
    <body>

      <section class="match-header">
        <div class="container results-shell">
          <p class="match-eyebrow mb-1">MATCHES</p>
          <h1 class="match-title mb-1">
            🔍 {heading}
          </h1>
          <p class="match-subtitle mb-0">Here are matching workers near you</p>

          <div class="match-tags d-flex flex-wrap gap-2 mt-2">
            <span class="match-tag">
              👥 <span>{matches_count} {match_label}</span>
            </span>
            <span class="match-tag">
              📍 <span>Within 105 km</span>
            </span>
            <span class="match-tag">
              🚗 <span>Driving time shown</span>
            </span>
          </div>
        </div>
      </section>

      <section class="container results-shell my-3 my-md-4">
        <div class="row g-3 g-md-4">
          {matched_list or "<div class='col-12'><div class='alert alert-warning text-center'>😞 No matching workers found within 105 km radius.</div></div>"}
        </div>

        <div class="text-center mt-4 mb-3">
          <a href="/provide_job" class="btn btn-outline-secondary px-4">
            🔁 Start a New Search
          </a>
        </div>
      </section>

      <script>

        async function getDrivingDistance(workerLat, workerLon, userLat, userLon, skillId, jobId) {{
          const spanId = "distance-" + skillId;
          const span = document.getElementById(spanId);
          const pill = document.getElementById("pill-distance-" + skillId);

          try {{
            const res = await fetch("/distance_cached", {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              credentials: "same-origin",
              body: JSON.stringify({{
                job_id: jobId,
                skill_id: skillId,
                user_lat: userLat,
                user_lon: userLon,
                worker_lat: workerLat,
                worker_lon: workerLon
              }})
            }});

            const data = await res.json();

            if (data.ok) {{
              const text = `${{data.distance_km}} km (~${{data.duration_min}} mins)`;
              if (span) span.textContent = text;
              if (pill) pill.textContent = `${{data.distance_km}} km`;
            }} else {{
              if (span) span.textContent = "❌ Not available";
              if (pill) pill.textContent = "--";
            }}

          }} catch (e) {{
            console.error("Distance error:", e);
            if (span) span.textContent = "⚠️ Error";
          }}
        }}


        document.addEventListener('DOMContentLoaded', function () {{
          document.querySelectorAll('.worker-card[data-href]').forEach(function (card) {{
            card.addEventListener('click', function (e) {{
              if (e.target.closest('a,button')) return;
              window.location = card.dataset.href;
            }});
          }});
        }});

        function startQuickCall(workerToken) {{
          fetch('/call_worker/' + workerToken, {{
            method: 'POST',
          }})
          .then(r => r.json())
          .then(j => {{
            if (j.status !== 'success') {{
              alert(j.message || 'Unable to start call.');
            }}
          }})
          .catch(() => alert('Network error while starting call.'));
        }}

        // Distance calls injected by Python:
        __DISTANCE_CALLS__
      </script>
    </body>
    </html>
    """

    # Inject the JS calls for each worker's distance
    # Inject the JS calls for each worker's distance
    calls = []
    for data in matched_workers.values():
        u = data["user"]

        skill_id = data["skill_id"]
        job_id = data.get("job_id")

        calls.append(
            f'getDrivingDistance({u.latitude}, {u.longitude}, {user_lat}, {user_lon}, {skill_id}, {job_id});'
        )

    html = html.replace("__DISTANCE_CALLS__", "\n        ".join(calls))
    return HTMLResponse(content=html)


@router.get("/jobs/by_category", response_class=HTMLResponse)
def jobs_by_category(
        request: Request,
        c: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> HTMLResponse:
    category = (c or "").strip()
    if not category:
        return HTMLResponse("<p>Error: category is required.</p>", status_code=400)

    user_lat = current_user.latitude
    user_lon = current_user.longitude
    if user_lat is None or user_lon is None:
        return HTMLResponse("<p>Error: Please enable location so we can find nearby workers.</p>", status_code=400)

    # lightweight job for worker page
    job = Job(
        title=f"{category.title()}",
        description=f"You are seeking help for: {category.title()}",
        user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    skills_q = (
        db.query(Skill)
        .filter(func.lower(func.trim(Skill.category)) == category.lower().strip())
        .all()
    )

    # NOTE: key is now skill.id -> each skill gets its own card
    matched_workers: Dict[int, dict] = {}
    for skill in skills_q:
        worker_user = db.get(User, skill.user_id)
        if not worker_user or worker_user.latitude is None or worker_user.longitude is None:
            continue

        profile = db.query(WorkerProfile).filter(
            WorkerProfile.user_id == worker_user.id
        ).first()

        # 🔒 MODERATION ENFORCEMENT
        if not profile:
            continue

        if profile.moderation_status in ("limited", "suspended", "banned"):
            continue

        if not profile.is_online or worker_user.busy:
            continue

        distance_km = geodesic(
            (user_lat, user_lon),
            (worker_user.latitude, worker_user.longitude),
        ).km

        if distance_km <= 105:
            matched_workers[skill.id] = {
                "user": worker_user,
                "skill_id": skill.id,
                "skill_name": skill.name,
                "skill_category": skill.category or "",
                "rate": getattr(skill, "rate", None),
                "rate_type": getattr(skill, "rate_type", None),
                "distance": round(distance_km, 2),
                "job_id": job.id,
            }

    heading = f"Category: {category.title()}"
    return _render_results_page(
        heading=heading,
        matched_workers=matched_workers,
        user_lat=user_lat,
        user_lon=user_lon,
    )
