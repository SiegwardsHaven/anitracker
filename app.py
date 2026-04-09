"""
app.py
------
Run with:  python app.py
Then open: http://127.0.0.1:5000
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from datetime import datetime
import os, uuid, threading, time
import requests as http_req
from werkzeug.utils import secure_filename

from dotenv import load_dotenv
load_dotenv()

import api_clients as api
import database as db
import auth_db
from mongo import init_indexes, avatar_cache_col

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# Initialize MongoDB indexes
init_indexes()

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."


# Context processor for global template variables
@app.context_processor
def inject_globals():
    return {'now': datetime.now()}


class User(UserMixin):
    def __init__(self, user_dict):
        self.id = str(user_dict["_id"])
        self.username = user_dict["username"]
        self.avatar_url = user_dict.get("avatar_url")


@login_manager.user_loader
def load_user(user_id):
    user_dict = auth_db.get_user_by_id(user_id)
    if user_dict:
        return User(user_dict)
    return None


# ------------------------------------------------------------------
# AUTHENTICATION ROUTES
# ------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        user_dict = auth_db.authenticate_user(username, password)
        if user_dict:
            user = User(user_dict)
            login_user(user, remember=request.form.get("remember") == "on")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("home"))
        else:
            flash("Invalid username or password", "error")
    
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        security_question = request.form.get("security_question", "").strip()
        security_answer = request.form.get("security_answer", "").strip()
        
        if not username or not password or not security_question or not security_answer:
            flash("All fields are required", "error")
        elif len(username) < 3 or len(username) > 30:
            flash("Username must be 3-30 characters", "error")
        elif password != confirm_password:
            flash("Passwords do not match", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters", "error")
        else:
            try:
                user_dict = auth_db.create_user(username, password, security_question, security_answer)
                flash("Account created successfully! Please log in.", "success")
                return redirect(url_for("login"))
            except ValueError as e:
                flash(str(e), "error")
    
    return render_template("register.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    
    security_question = None
    username_value = request.args.get("username", "") or request.form.get("username", "")
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        security_answer = request.form.get("security_answer", "").strip()
        
        # If only username is provided, fetch the security question
        if username and not security_answer:
            user = auth_db.get_user_by_username(username)
            if user:
                sq = auth_db.get_security_question(username)
                if sq:
                    security_question = sq
                    username_value = username
                else:
                    flash("No security question set for this account", "error")
            else:
                flash("Username not found", "error")
        # If both username and answer provided, verify
        elif username and security_answer:
            if auth_db.verify_security_answer(username, security_answer):
                try:
                    token = auth_db.create_reset_token(username)
                    flash("Security answer verified! Please set your new password.", "success")
                    return redirect(url_for("reset_password", token=token))
                except ValueError:
                    flash("An error occurred. Please try again.", "error")
            else:
                flash("Invalid username or security answer", "error")
                # Re-fetch security question to show again
                sq = auth_db.get_security_question(username)
                if sq:
                    security_question = sq
                    username_value = username
        else:
            flash("Please enter your username", "error")
    
    return render_template("forgot_password.html", security_question=security_question, username=username_value)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    
    user = auth_db.verify_reset_token(token)
    if not user:
        flash("Invalid or expired reset link", "error")
        return redirect(url_for("forgot_password"))
    
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        if password != confirm_password:
            flash("Passwords do not match", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters", "error")
        else:
            if auth_db.reset_password(token, password):
                flash("Password reset successfully! Please log in.", "success")
                return redirect(url_for("login"))
            else:
                flash("Failed to reset password", "error")
    
    return render_template("reset_password.html", token=token)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# ------------------------------------------------------------------
# HOME
# ------------------------------------------------------------------
@app.route("/")
@login_required
def home():
    return render_template("home.html")


# ------------------------------------------------------------------
# SEARCH (with advanced filters)
# ------------------------------------------------------------------
@app.route("/search")
@login_required
def search():
    kind     = request.args.get("kind", "anime")           # 'anime' | 'manga'
    q        = request.args.get("q", "").strip()
    genres   = request.args.get("genres", "")
    min_score= request.args.get("min_score", "")
    status   = request.args.get("status", "")
    type_    = request.args.get("type", "")
    order_by = request.args.get("order_by", "members")
    sort     = request.args.get("sort", "desc")

    results = []
    if q or genres or min_score or status or type_:
        try:
            if kind == "manga":
                results = api.search_manga(
                    q, genres=genres, min_score=min_score, status=status,
                    type_=type_, order_by=order_by, sort=sort)
            else:
                results = api.search_anime(
                    q, genres=genres, min_score=min_score, status=status,
                    type_=type_, order_by=order_by, sort=sort)
        except Exception as e:
            return render_template("search.html", error=str(e),
                                   results=[], kind=kind, genres_list=[], carousel=[])

    try:
        genres_list = api.get_genres(kind)
    except Exception:
        genres_list = []

    # Get carousel items
    carousel = []
    try:
        carousel = api.get_top_rated_carousel(kind, limit=20)
    except Exception:
        pass

    return render_template(
        "search.html",
        results=results, kind=kind, q=q,
        selected_genres=genres, min_score=min_score,
        status=status, type_=type_, order_by=order_by, sort=sort,
        genres_list=genres_list, carousel=carousel,
    )


# ------------------------------------------------------------------
# DETAIL PAGES (the "profile cards")
# ------------------------------------------------------------------
@app.route("/anime/<int:mal_id>")
@login_required
def anime_detail(mal_id):
    data = api.get_anime(mal_id)
    if not data:
        abort(404)
    extras   = api.anilist_extras(mal_id, "ANIME")
    entry = db.get_anime_entry(current_user.id, mal_id)
    return render_template(
        "detail.html",
        kind="anime", data=data, extras=extras,
        episodes=[], entry=entry,
        statuses=db.ANIME_STATUSES, status_colors=db.STATUS_COLORS,
    )


@app.route("/manga/<int:mal_id>")
@login_required
def manga_detail(mal_id):
    data = api.get_manga(mal_id)
    if not data:
        abort(404)
    extras = api.anilist_extras(mal_id, "MANGA")
    entry = db.get_manga_entry(current_user.id, mal_id)
    return render_template(
        "detail.html",
        kind="manga", data=data, extras=extras,
        episodes=[], entry=entry,
        statuses=db.MANGA_STATUSES, status_colors=db.STATUS_COLORS,
    )


# ------------------------------------------------------------------
# MY LIST
# ------------------------------------------------------------------
@app.route("/list")
@login_required
def my_list():
    kind = request.args.get("kind", "anime")
    status_filter = request.args.get("status", "")
    if kind == "manga":
        all_items = db.list_manga(current_user.id)
        statuses = db.MANGA_STATUSES
    else:
        all_items = db.list_anime(current_user.id)
        statuses = db.ANIME_STATUSES

    # backfill title_english / title_japanese for older entries (max 5 per load)
    backfilled = 0
    for it in all_items:
        if backfilled >= 5:
            break
        if it.get("title_english") and it.get("title_japanese"):
            continue
        try:
            data = api.get_manga(it["mal_id"]) if kind == "manga" else api.get_anime(it["mal_id"])
            if data:
                te = data.get("title_english") or ""
                tj = data.get("title_japanese") or ""
                if te or tj:
                    upd = {}
                    if te:
                        upd["title_english"] = te
                        it["title_english"] = te
                    if tj:
                        upd["title_japanese"] = tj
                        it["title_japanese"] = tj
                    if upd:
                        col = db.manga_list_col if kind == "manga" else db.anime_list_col
                        col.update_one(
                            {"user_id": current_user.id, "mal_id": it["mal_id"]},
                            {"$set": upd},
                        )
                        backfilled += 1
        except Exception:
            pass
    # counts per status
    status_counts = {s: len([i for i in all_items if i["status"] == s]) for s in statuses}
    # filter
    if status_filter and status_filter in statuses:
        items = [i for i in all_items if i["status"] == status_filter]
    else:
        items = all_items
        status_filter = ""
    return render_template("list.html", kind=kind, items=items,
                           statuses=statuses, status_colors=db.STATUS_COLORS,
                           status_counts=status_counts, status_filter=status_filter,
                           total=len(all_items))


# ---- list mutation endpoints (called from JS) ----
@app.post("/api/list/anime")
@login_required
def api_save_anime():
    p = request.get_json(force=True)
    total_eps = int(p.get("total_eps") or 0)
    progress = max(0, int(p.get("progress") or 0))
    if total_eps > 0:
        progress = min(progress, total_eps)
    db.upsert_anime(
        user_id=current_user.id,
        mal_id=int(p["mal_id"]),
        title=p["title"],
        cover_url=p.get("cover_url"),
        total_eps=total_eps,
        status=p["status"],
        progress=progress,
        score=int(p["score"]) if p.get("score") else None,
        title_english=p.get("title_english") or None,
        title_japanese=p.get("title_japanese") or None,
        notes=p.get("notes") if "notes" in p else None,
    )
    return jsonify(ok=True)


@app.post("/api/list/manga")
@login_required
def api_save_manga():
    p = request.get_json(force=True)
    total_chs = int(p.get("total_chs") or 0)
    progress = max(0, int(p.get("progress") or 0))
    if total_chs > 0:
        progress = min(progress, total_chs)
    db.upsert_manga(
        user_id=current_user.id,
        mal_id=int(p["mal_id"]),
        title=p["title"],
        cover_url=p.get("cover_url"),
        total_chs=total_chs,
        status=p["status"],
        progress=progress,
        score=int(p["score"]) if p.get("score") else None,
        title_english=p.get("title_english") or None,
        title_japanese=p.get("title_japanese") or None,
        notes=p.get("notes") if "notes" in p else None,
    )
    return jsonify(ok=True)


@app.post("/api/list/anime/<int:mal_id>/delete")
@login_required
def api_delete_anime(mal_id):
    db.remove_anime(current_user.id, mal_id)
    return jsonify(ok=True)


@app.post("/api/list/manga/<int:mal_id>/delete")
@login_required
def api_delete_manga(mal_id):
    db.remove_manga(current_user.id, mal_id)
    return jsonify(ok=True)


@app.get("/api/carousel/<kind>")
def api_carousel(kind):
    """API endpoint for refreshing carousel data."""
    if kind not in ["anime", "manga"]:
        return jsonify(error="Invalid kind"), 400
    try:
        carousel = api.get_top_rated_carousel(kind, limit=20)
        return jsonify(items=carousel)
    except Exception as e:
        return jsonify(error=str(e)), 500


# ------------------------------------------------------------------
# STATISTICS
# ------------------------------------------------------------------
@app.route("/statistics")
@login_required
def statistics():
    anime_items = db.list_anime(current_user.id)
    manga_items = db.list_manga(current_user.id)

    # Anime stats
    anime_stats = {
        "total": len(anime_items),
        "by_status": {},
        "avg_score": 0,
        "scored_count": 0,
        "total_episodes": sum(i.get("progress", 0) for i in anime_items),
        "score_distribution": {str(s): 0 for s in range(1, 11)},
    }
    scored = [i["score"] for i in anime_items if i.get("score")]
    anime_stats["avg_score"] = round(sum(scored) / len(scored), 1) if scored else 0
    anime_stats["scored_count"] = len(scored)
    for s in db.ANIME_STATUSES:
        anime_stats["by_status"][s] = len([i for i in anime_items if i["status"] == s])
    for sc in scored:
        anime_stats["score_distribution"][str(sc)] += 1

    # Manga stats
    manga_stats = {
        "total": len(manga_items),
        "by_status": {},
        "avg_score": 0,
        "scored_count": 0,
        "total_chapters": sum(i.get("progress", 0) for i in manga_items),
        "score_distribution": {str(s): 0 for s in range(1, 11)},
    }
    scored_m = [i["score"] for i in manga_items if i.get("score")]
    manga_stats["avg_score"] = round(sum(scored_m) / len(scored_m), 1) if scored_m else 0
    manga_stats["scored_count"] = len(scored_m)
    for s in db.MANGA_STATUSES:
        manga_stats["by_status"][s] = len([i for i in manga_items if i["status"] == s])
    for sc in scored_m:
        manga_stats["score_distribution"][str(sc)] += 1

    return render_template("statistics.html",
                           anime_stats=anime_stats,
                           manga_stats=manga_stats,
                           anime_statuses=db.ANIME_STATUSES,
                           manga_statuses=db.MANGA_STATUSES,
                           status_colors=db.STATUS_COLORS)


# ------------------------------------------------------------------
# PROFILE
# ------------------------------------------------------------------

# Upload config
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'avatars')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2 MB
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Character IDs (MAL) grouped by anime
AVATAR_CHAR_DATA = {
    "Dragon Ball Z": [
        (246, "Goku"), (913, "Vegeta"), (2093, "Gohan"), (914, "Piccolo"),
        (143844, "Future Trunks"), (3694, "Frieza"), (2707, "Trunks"),
        (678, "Bulma"), (2159, "Krillin"), (3908, "Cell"),
    ],
    "Naruto": [
        (17, "Naruto"), (13, "Sasuke"), (85, "Kakashi"), (14, "Itachi"),
        (2007, "Shikamaru"), (2423, "Jiraiya"), (1662, "Gaara"),
        (1555, "Hinata"), (2535, "Minato"), (3180, "Pain"),
    ],
    "One Piece": [
        (40, "Luffy"), (62, "Zoro"), (723, "Nami"), (305, "Sanji"),
        (61, "Robin"), (64, "Franky"), (5627, "Brook"),
        (309, "Chopper"), (724, "Usopp"), (18938, "Jinbe"),
    ],
    "Bleach": [
        (5, "Ichigo"), (6, "Rukia"), (210, "Urahara"), (909, "Kenpachi"),
        (1086, "Aizen"), (1081, "Ulquiorra"), (245, "Toshiro"),
        (907, "Byakuya"), (1080, "Grimmjow"), (908, "Yoruichi"),
    ],
    "Attack on Titan": [
        (45627, "Levi"), (40882, "Eren"), (40881, "Mikasa"),
        (46494, "Armin"), (46496, "Erwin"), (71121, "Hange"),
        (45887, "Sasha"), (46484, "Reiner"), (46498, "Jean"),
        (46490, "Annie"),
    ],
    "Demon Slayer": [
        (146156, "Tanjiro"), (146157, "Nezuko"), (146158, "Zenitsu"),
        (146159, "Inosuke"), (151143, "Rengoku"), (146736, "Shinobu"),
        (146735, "Giyu"), (151144, "Tengen"), (151145, "Mitsuri"),
        (151147, "Muichiro"),
    ],
    "Jujutsu Kaisen": [
        (164471, "Gojo"), (163847, "Yuji"), (164470, "Megumi"),
        (164472, "Nobara"), (164473, "Nanami"), (175198, "Sukuna"),
        (164482, "Maki"), (164478, "Inumaki"), (168067, "Yuta"),
        (175542, "Geto"),
    ],
    "My Dress-Up Darling": [
        (166439, "Marin"), (166438, "Wakana"), (193037, "Sajuna"),
        (195814, "Shinju"), (205322, "Nowa"), (204013, "Kaoru"),
        (207323, "Liz"), (259071, "Shizuku"), (206482, "Mirai"),
        (206117, "Neon"),
    ],
    "Frieren": [
        (184947, "Frieren"), (188176, "Fern"), (188177, "Stark"),
        (186854, "Himmel"), (206725, "Ubel"), (196825, "Eisen"),
        (187307, "Flamme"), (196826, "Heiter"), (196912, "Sein"),
        (215250, "Denken"),
    ],
    "Cowboy Bebop": [
        (1, "Spike"), (2, "Faye"), (3, "Jet"), (16, "Ed"),
        (4, "Ein"), (2734, "Vicious"), (2735, "Julia"),
        (2736, "Gren"), (29313, "Mad Pierrot"), (23740, "Andy"),
    ],
}


def _prefetch_avatars():
    """Background thread: fetch character images from Jikan and cache in MongoDB."""
    for anime, chars in AVATAR_CHAR_DATA.items():
        for char_id, name in chars:
            if avatar_cache_col.find_one({"char_id": char_id}):
                continue
            try:
                resp = http_req.get(
                    f"https://api.jikan.moe/v4/characters/{char_id}",
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    img = (data.get("images", {}).get("jpg", {}).get("image_url")
                           or data.get("images", {}).get("webp", {}).get("image_url"))
                    if img:
                        avatar_cache_col.update_one(
                            {"char_id": char_id},
                            {"$set": {
                                "char_id": char_id,
                                "name": name,
                                "anime": anime,
                                "url": img,
                            }},
                            upsert=True,
                        )
                time.sleep(0.4)
            except Exception:
                pass


# Start prefetch in background (skip Werkzeug reloader child)
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    threading.Thread(target=_prefetch_avatars, daemon=True).start()


def _get_grouped_avatars():
    """Return cached avatar data grouped by anime."""
    grouped = {}
    for anime, chars in AVATAR_CHAR_DATA.items():
        grouped[anime] = []
        for char_id, name in chars:
            doc = avatar_cache_col.find_one({"char_id": char_id})
            grouped[anime].append({
                "name": name,
                "anime": anime,
                "url": doc["url"] if doc else None,
                "char_id": char_id,
            })
    return grouped


@app.route("/profile")
@login_required
def profile():
    profile_data = auth_db.get_user_profile(current_user.id)

    return render_template("profile.html",
                           profile=profile_data,
                           grouped_avatars=_get_grouped_avatars())


@app.post("/api/profile/avatar")
@login_required
def api_update_avatar():
    p = request.get_json(force=True)
    avatar_url = p.get("avatar_url", "").strip()
    if not avatar_url:
        return jsonify(ok=False, error="No avatar URL"), 400
    auth_db.update_avatar(current_user.id, avatar_url)
    return jsonify(ok=True)


@app.post("/api/profile/upload-avatar")
@login_required
def api_upload_avatar():
    if "file" not in request.files:
        return jsonify(ok=False, error="No file"), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify(ok=False, error="No file selected"), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(ok=False, error="Invalid file type"), 400
    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
    f.save(filepath)
    avatar_url = url_for("static", filename=f"uploads/avatars/{filename}")
    auth_db.update_avatar(current_user.id, avatar_url)
    return jsonify(ok=True, url=avatar_url)


@app.post("/api/profile/security-question")
@login_required
def api_update_security_question():
    p = request.get_json(force=True)
    question = p.get("question", "").strip()
    answer = p.get("answer", "").strip()
    if not question or not answer:
        return jsonify(ok=False, error="Question and answer required"), 400
    auth_db.update_security_question(current_user.id, question, answer)
    return jsonify(ok=True)


@app.post("/api/profile/wipe-data")
@login_required
def api_wipe_data():
    auth_db.wipe_user_data(current_user.id)
    return jsonify(ok=True)


@app.post("/api/profile/delete-account")
@login_required
def api_delete_account():
    auth_db.delete_user_account(current_user.id)
    logout_user()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True)
