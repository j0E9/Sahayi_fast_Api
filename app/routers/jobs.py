# app/routers/jobs.py
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from geopy.distance import geodesic
from sqlalchemy import func
from app.database import get_db
from app.models import User, Skill, WorkerProfile, Job
from app.settings import settings

router = APIRouter(tags=["jobs"])


# --------- session-based auth (Trust API style) ----------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Query by primary key (SQLAlchemy 2.x compatible)
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ------------------ GET form ------------------
@router.get("/provide_job", response_class=HTMLResponse)
@router.get("/provide_job/", response_class=HTMLResponse)  # allow trailing slash
def provide_job_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    skills = db.query(Skill).all()
    skill_names = [ (s.name or "").strip().lower() for s in skills ]

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


# ------------------ POST search ------------------
@router.post("/provide_job", response_class=HTMLResponse)
@router.post("/provide_job/", response_class=HTMLResponse)  # allow trailing slash
def provide_job_submit(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    form = dict((request.scope.get("form") or {}))  # not used; we’ll parse below using starlette
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

    # Match workers by skill keywords
    job_keywords = set(job_type_raw.split())
    skills = db.query(Skill).all()

    matched_workers: Dict[int, dict] = {}
    for skill in skills:
        skill_name = (skill.name or "").strip().lower()
        if not skill_name:
            continue
        if job_keywords & set(skill_name.split()):
            worker_user = db.get(User, skill.user_id)
            if not worker_user or not worker_user.latitude or not worker_user.longitude:
                continue

            profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == worker_user.id).first()
            if not profile or not profile.is_online or worker_user.busy:
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
                    "rate": getattr(skill, "rate", None),
                    "rate_type": getattr(skill, "rate_type", None),
                    "distance": round(distance_km, 2),
                }

    matched_list = ""
    for data in matched_workers.values():
        rate = f"₹{data['rate']} / {data['rate_type']}" if data["rate"] is not None and data["rate_type"] else "N/A"
        matched_list += f"""
        <div class="col-12 col-md-6 col-lg-4">
            <div class="card shadow-sm mb-4">
                <div class="card-body">
                    <h5 class="card-title">{data["user"].name}</h5>
                    <p class="card-text">
                        <b>Skill:</b> {(data["skill_name"] or "").title()}<br>
                        <b>Rate:</b> {rate}<br>
                        <b>Driving Distance:</b>
                        <span id="distance-{data["user"].id}">Calculating...</span>
                    </p>
                    <a href="/worker/{data["user"].id}?job_id={job.id}&skill_id={data["skill_id"]}" class="btn btn-primary w-100">👤 View Profile</a>
                </div>
            </div>
        </div>
        """

    MAPBOX = settings.MAPBOX_ACCESS_TOKEN or ""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Matching Workers</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{
                background-color: #f2f2f2;
                font-family: 'Segoe UI', sans-serif;
            }}
            .card:hover {{
                transform: scale(1.02);
                box-shadow: 0 8px 16px rgba(0,0,0,0.1);
                transition: all 0.3s ease-in-out;
            }}
            .title-banner {{
                background: linear-gradient(135deg, #007bff, #00c6ff);
                color: white;
                padding: 30px;
                text-align: center;
                border-radius: 0 0 12px 12px;
            }}
            .title-banner h2 {{
                font-size: 28px;
                margin-bottom: 10px;
            }}
            .title-banner p {{
                font-size: 18px;
                margin-bottom: 0;
            }}
        </style>
    </head>
    <body>
        <div class="title-banner">
            <h2>🔍 You searched for: <b>{job_type_raw.title()}</b></h2>
            <p>Here are matching workers near you</p>
        </div>

        <div class="container mt-4">
            <div class="row">
                {matched_list or "<div class='col-12'><div class='alert alert-warning text-center'>😞 No matching workers found within 105 km radius.</div></div>"}
            </div>
            <div class="text-center mt-4">
                <a href="/provide_job" class="btn btn-outline-primary">🔄 Start a New Search</a>
            </div>
        </div>

        <script>
        const MAPBOX_TOKEN = "{MAPBOX}";

        async function getDrivingDistance(workerLat, workerLon, userLat, userLon, spanId) {{
            const url = `https://api.mapbox.com/directions/v5/mapbox/driving/${{userLon}},${{userLat}};${{workerLon}},${{workerLat}}?access_token=${{MAPBOX_TOKEN}}&overview=false`;
            try {{
                const res = await fetch(url);
                const data = await res.json();
                const span = document.getElementById(spanId);
                if (data.code === "Ok" && data.routes && data.routes.length > 0) {{
                    const distanceKm = (data.routes[0].distance / 1000).toFixed(2);
                    const durationMin = (data.routes[0].duration / 60).toFixed(1);
                    span.textContent = `${{distanceKm}} km (~${{durationMin}} mins)`;
                }} else {{
                    span.textContent = "❌ Not available";
                }}
            }} catch (err) {{
                console.error(err);
                const el = document.getElementById(spanId);
                if (el) el.textContent = "⚠️ Error";
            }}
        }}

        {(lambda _vals: "".join([
            f'getDrivingDistance({d["user"].latitude}, {d["user"].longitude}, {user_lat}, {user_lon}, "distance-{d["user"].id}");\n'
            for d in _vals
        ]))(list({k:v for k,v in matched_workers.items()}.values()))}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)




def _render_results_page(
    *, heading: str, matched_workers: Dict[int, dict], user_lat: float, user_lon: float
) -> HTMLResponse:
    from app.settings import settings

    MAPBOX = settings.MAPBOX_ACCESS_TOKEN or ""
    matches_count = len(matched_workers)

    # Build worker cards
    matched_list = ""
    for data in matched_workers.values():
        rate = (
            f"₹{data['rate']} / {data['rate_type']}"
            if data.get("rate") is not None and data.get("rate_type")
            else "N/A"
        )
        user = data["user"]
        skill_name = (data["skill_name"] or "").title()
        job_id = data.get("job_id", "")

        # use a key per skill (unique per card)
        key = data["skill_id"]

        matched_list += f"""
        <div class="col-12 col-md-4">
          <article
            class="worker-card h-100"
            data-href="/worker/{user.id}?job_id={job_id}&skill_id={data['skill_id']}"
          >
            <div class="card-top-row d-flex align-items-center mb-2">
              <div class="worker-avatar me-2">
                {user.name[:1].upper()}
              </div>
              <div class="flex-grow-1">
                <h2 class="worker-name mb-0">{user.name}</h2>
                <div class="worker-skill small text-muted">{skill_name}</div>
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
                href="/worker/{user.id}?job_id={job_id}&skill_id={data['skill_id']}"
                class="btn btn-primary btn-sm flex-fill"
              >
                👤 View Profile
              </a>
              <button
                type="button"
                class="btn btn-outline-primary btn-sm flex-fill"
                onclick="startQuickCall({user.id})"
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
              👥 <span>{matches_count} match{'es' if matches_count != 1 else ''}</span>
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
        const MAPBOX_TOKEN = "{MAPBOX}";

        async function getDrivingDistance(workerLat, workerLon, userLat, userLon, spanId) {{
          const url = `https://api.mapbox.com/directions/v5/mapbox/driving/${{userLon}},${{userLat}};${{workerLon}},${{workerLat}}?access_token=${{MAPBOX_TOKEN}}&overview=false`;
          try {{
            const res = await fetch(url);
            const data = await res.json();
            const span = document.getElementById(spanId);
            const pill = document.getElementById('pill-distance-' + spanId.split('-')[1]);
            if (data.code === "Ok" && data.routes && data.routes.length > 0) {{
              const distanceKm = (data.routes[0].distance / 1000).toFixed(2);
              const durationMin = (data.routes[0].duration / 60).toFixed(1);
              const text = `${{distanceKm}} km (~${{durationMin}} mins)`;
              if (span) span.textContent = text;
              if (pill) pill.textContent = `${{distanceKm}} km`;
            }} else {{
              if (span) span.textContent = "❌ Not available";
              if (pill) pill.textContent = "--";
            }}
          }} catch (err) {{
            console.error(err);
            const el = document.getElementById(spanId);
            if (el) el.textContent = "⚠️ Error";
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

        function startQuickCall(workerId) {{
          fetch('/call_worker/' + workerId, {{
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
    calls = []
    for data in matched_workers.values():
        u = data["user"]
        key = data["skill_id"]          # use same key as IDs
        calls.append(
            f'getDrivingDistance({u.latitude}, {u.longitude}, {user_lat}, {user_lon}, "distance-{key}");'
        )
    html = html.replace("__DISTANCE_CALLS__", "\n        ".join(calls))

    return HTMLResponse(content=html)


from typing import Dict


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

        profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == worker_user.id).first()
        if not profile or not profile.is_online or worker_user.busy:
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
