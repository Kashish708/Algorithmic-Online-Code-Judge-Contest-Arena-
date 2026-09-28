import datetime
import asyncio
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import engine, Base, SessionLocal, get_db
import models
from judge import execute_all_test_cases
from seed import seed_data

Base.metadata.create_all(bind=engine)
seed_data()

app = FastAPI(title="Advanced Online Judge & Contest System")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Security & JWT Config ---
SECRET_KEY = "supersecretjudgekey_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)
def get_password_hash(password): return pwd_context.hash(password)
def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- WebSocket Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)

    async def send_message(self, message: dict, client_id: str):
        ws = self.active_connections.get(client_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(client_id)

manager = ConnectionManager()

# --- Pydantic Schemas ---
class UserAuth(BaseModel): username: str; password: str
class TestCaseCreate(BaseModel): input_data: str; expected_output: str; weight: int = 10; is_hidden: bool = False
class ProblemCreate(BaseModel): title: str; description: str; time_limit: float = 2.0; memory_limit_mb: float = 256.0; checker_type: str = "exact"; test_cases: List[TestCaseCreate]
class ContestCreate(BaseModel): title: str; start_time: datetime.datetime; end_time: datetime.datetime; freeze_time: datetime.datetime
class SubmissionCreate(BaseModel): problem_id: int; language: str; code: str; contest_id: Optional[int] = None; client_id: Optional[str] = None

# --- Background Worker ---
def process_submission_task(submission_id: int, client_id: str, loop: asyncio.AbstractEventLoop):
    db: Session = SessionLocal()
    try:
        sub = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
        problem = db.query(models.Problem).filter(models.Problem.id == sub.problem_id).first()
        test_cases = db.query(models.TestCase).filter(models.TestCase.problem_id == problem.id).all()

        def progress_cb(msg):
            if client_id:
                try:
                    asyncio.run_coroutine_threadsafe(manager.send_message({"type": "progress", "text": msg}, client_id), loop)
                except Exception:
                    pass

            try:
                sub.status = msg
                db.commit()
            except Exception:
                pass

        try:
            result = execute_all_test_cases(
                code=sub.code, language=sub.language, test_cases=test_cases,
                time_limit=problem.time_limit, mem_limit_mb=problem.memory_limit_mb,
                checker_type=problem.checker_type, progress_cb=progress_cb
            )

            sub.status = result["status"]
            sub.score = result["score"]
            sub.total_score = result["total_score"]
            sub.execution_time_ms = result["time_ms"]
            sub.peak_memory_mb = result["memory_mb"]
            sub.error = result["error"]
            db.commit()

        except Exception as e:
            try:
                db.rollback()
                sub.status = "System Error"
                sub.error = str(e)
                db.commit()
            except Exception:
                pass

        if client_id:
            try:
                asyncio.run_coroutine_threadsafe(manager.send_message({
                    "type": "result", "status": sub.status, "score": sub.score, "total_score": sub.total_score,
                    "execution_time_ms": sub.execution_time_ms, "peak_memory_mb": sub.peak_memory_mb, "error": sub.error
                }, client_id), loop)
            except Exception:
                pass
    finally:
        db.close()

# --- WebSocket Endpoint ---
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(client_id)

# --- Auth Endpoints ---
@app.post("/register")
def register_user(user: UserAuth, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    new_user = models.User(username=user.username, hashed_password=get_password_hash(user.password))
    db.add(new_user)
    db.commit()
    return {"message": "User registered successfully", "access_token": create_access_token({"sub": new_user.username}), "username": new_user.username}

@app.post("/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    return {"access_token": create_access_token({"sub": user.username}), "token_type": "bearer", "username": user.username}

# --- Standard REST Endpoints ---
@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

@app.post("/problems")
def create_problem(payload: ProblemCreate, db: Session = Depends(get_db)):
    prob = models.Problem(
        title=payload.title,
        description=payload.description,
        time_limit=payload.time_limit,
        memory_limit_mb=payload.memory_limit_mb,
        checker_type=payload.checker_type
    )
    db.add(prob)
    db.commit()
    db.refresh(prob)
    for tc in payload.test_cases:
        db.add(models.TestCase(
            problem_id=prob.id,
            input_data=tc.input_data,
            expected_output=tc.expected_output,
            weight=tc.weight,
            is_hidden=tc.is_hidden
        ))
    db.commit()
    return {"message": "Problem created", "problem_id": prob.id}

@app.get("/problems")
def get_problems(db: Session = Depends(get_db)):
    return [
        {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "time_limit": p.time_limit,
            "memory_limit_mb": p.memory_limit_mb,
            "checker_type": p.checker_type,
            "total_test_cases": len(p.test_cases),
            "sample_cases": [
                {"input": tc.input_data, "output": tc.expected_output, "weight": tc.weight}
                for tc in p.test_cases if not tc.is_hidden
            ]
        }
        for p in db.query(models.Problem).all()
    ]

@app.get("/problems/{problem_id}/leaderboard")
def get_problem_leaderboard(problem_id: int, db: Session = Depends(get_db)):
    subs = db.query(models.Submission).filter(
        models.Submission.problem_id == problem_id,
        models.Submission.status == "Accepted"
    ).order_by(
        models.Submission.execution_time_ms.asc(),
        models.Submission.peak_memory_mb.asc(),
        models.Submission.created_at.asc()
    ).limit(25).all()

    return [
        {
            "rank": idx + 1,
            "username": s.user.username if s.user else "Anonymous",
            "language": s.language,
            "execution_time_ms": s.execution_time_ms,
            "peak_memory_mb": s.peak_memory_mb,
            "submitted_at": s.created_at
        }
        for idx, s in enumerate(subs)
    ]

@app.post("/contests")
def create_contest(payload: ContestCreate, db: Session = Depends(get_db)):
    contest = models.Contest(
        title=payload.title,
        start_time=payload.start_time,
        end_time=payload.end_time,
        freeze_time=payload.freeze_time
    )
    db.add(contest)
    db.commit()
    db.refresh(contest)
    return {"message": "Contest created", "contest_id": contest.id}

@app.get("/contests")
def list_contests(db: Session = Depends(get_db)):
    return db.query(models.Contest).all()

@app.get("/contests/{contest_id}/leaderboard")
def get_contest_leaderboard(contest_id: int, db: Session = Depends(get_db)):
    contest = db.query(models.Contest).filter(models.Contest.id == contest_id).first()
    if not contest:
        raise HTTPException(status_code=404, detail="Contest not found")
    now = datetime.datetime.utcnow()
    cutoff = contest.freeze_time if (contest.freeze_time <= now < contest.end_time) else contest.end_time
    submissions = db.query(models.Submission).filter(
        models.Submission.contest_id == contest_id,
        models.Submission.created_at >= contest.start_time,
        models.Submission.created_at <= cutoff
    ).order_by(models.Submission.created_at.asc()).all()

    users_data = {}
    for sub in submissions:
        user_id = sub.user_id or 0
        if user_id not in users_data:
            users_data[user_id] = {
                "username": sub.user.username if sub.user else "Anonymous",
                "solved_problems": set(),
                "penalty_minutes": 0,
                "wrong_attempts": {}
            }
        if sub.problem_id in users_data[user_id]["solved_problems"]:
            continue
        if sub.status == "Accepted":
            users_data[user_id]["solved_problems"].add(sub.problem_id)
            users_data[user_id]["penalty_minutes"] += int((sub.created_at - contest.start_time).total_seconds() / 60) + (users_data[user_id]["wrong_attempts"].get(sub.problem_id, 0) * 20)
        else:
            users_data[user_id]["wrong_attempts"][sub.problem_id] = users_data[user_id]["wrong_attempts"].get(sub.problem_id, 0) + 1

    ranked = sorted(users_data.values(), key=lambda u: (-len(u["solved_problems"]), u["penalty_minutes"]))
    return {
        "contest_title": contest.title,
        "is_frozen": contest.freeze_time <= now < contest.end_time,
        "standings": [
            {
                "rank": i + 1,
                "username": u["username"],
                "problems_solved": len(u["solved_problems"]),
                "total_penalty": u["penalty_minutes"]
            }
            for i, u in enumerate(ranked)
        ]
    }

@app.post("/submissions")
async def create_submission(
    payload: SubmissionCreate, 
    bg: BackgroundTasks, 
    token: Optional[str] = Depends(oauth2_scheme), 
    db: Session = Depends(get_db)
):
    prob = db.query(models.Problem).filter(models.Problem.id == payload.problem_id).first()
    if not prob:
        raise HTTPException(status_code=404, detail="Problem not found")

    user_id = None
    if token:
        try:
            tok_data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user = db.query(models.User).filter(models.User.username == tok_data.get("sub")).first()
            if user:
                user_id = user.id
        except JWTError:
            pass

    sub = models.Submission(
        user_id=user_id,
        contest_id=payload.contest_id,
        problem_id=payload.problem_id,
        language=payload.language,
        code=payload.code,
        status="Queued",
        total_score=sum(tc.weight for tc in prob.test_cases)
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    loop = asyncio.get_running_loop()
    bg.add_task(process_submission_task, sub.id, payload.client_id, loop)
    return {"submission_id": sub.id, "status": "Queued"}

@app.get("/submissions")
def get_submission_history(db: Session = Depends(get_db)):
    return db.query(models.Submission).order_by(models.Submission.created_at.desc()).limit(20).all()