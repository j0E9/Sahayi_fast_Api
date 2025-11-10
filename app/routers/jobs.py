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




# --- helper to render results (same look as your POST) ---
def _render_results_page(
    *, heading: str, matched_workers: Dict[int, dict], user_lat: float, user_lon: float
) -> HTMLResponse:
    from app.settings import settings
    MAPBOX = settings.MAPBOX_ACCESS_TOKEN or ""

    matched_list = ""
    for data in matched_workers.values():
        rate = f"₹{data['rate']} / {data['rate_type']}" if data["rate"] and data["rate_type"] else "N/A"
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
              <a href="/worker/{data["user"].id}?job_id={data.get("job_id","")}&skill_id={data["skill_id"]}" class="btn btn-primary w-100">👤 View Profile</a>
            </div>
          </div>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Matching Workers</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      <style>
        body {{ background:#f2f2f2; font-family: 'Segoe UI', sans-serif; }}
        .card:hover {{ transform:scale(1.02); box-shadow:0 8px 16px rgba(0,0,0,0.1); transition:all .3s; }}
        .title-banner {{ background:linear-gradient(135deg,#007bff,#00c6ff); color:#fff; padding:30px; text-align:center; border-radius:0 0 12px 12px; }}
        .title-banner h2 {{ font-size:28px; margin-bottom:10px; }}
        .title-banner p {{ font-size:18px; margin-bottom:0; }}
      </style>
    </head>
    <body>
      <div class="title-banner">
        <h2>🔎 {heading}</h2>
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
            const el = document.getElementById(spanId);
            if (el) el.textContent = "⚠️ Error";
          }}
        }}
        {{}}
      </script>
    </body>
    </html>
    """

    # inject calls for distance calculation
    calls = []
    for data in matched_workers.values():
        calls.append(
            f'getDrivingDistance({data["user"].latitude}, {data["user"].longitude}, {user_lat}, {user_lon}, "distance-{data["user"].id}");'
        )
    html = html.replace("{}", "\n".join(calls))
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

    # ✅ Create a lightweight job so the worker page has job_id
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

        if distance_km <= 105 and worker_user.id not in matched_workers:
            matched_workers[worker_user.id] = {
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
