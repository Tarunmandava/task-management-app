import os
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Response, Cookie, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.cloud import firestore
from google.oauth2 import service_account
import uvicorn
import firebase_admin
from firebase_admin import credentials, auth
from dateutil.parser import parse as parse_date
from typing import List, Optional

# Initialize FastAPI app
app = FastAPI()

# Mount static directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-admin-sdk.json")
    firebase_admin.initialize_app(cred)

# Initialize Firestore DB
db_credentials = service_account.Credentials.from_service_account_file('service-account.json')
db = firestore.Client(credentials=db_credentials)

# Helper function to verify Firebase ID token
async def verify_user(request: Request) -> str:
    user_id = request.cookies.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please log in to continue")
    return user_id

# Helper function to parse and validate due date
def parse_due_date(due_date: Optional[str]) -> Optional[datetime]:
    if not due_date:
        return None
    try:
        parsed_date = parse_date(due_date)
        if parsed_date < datetime.now():
            raise ValueError("Due date cannot be in the past")
        return parsed_date
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid due date format: {str(e)}")

# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/verify-token")
async def verify_token(request: Request):
    data = await request.json()
    id_token = data.get("idToken")
    
    if not id_token:
        return JSONResponse(content={"success": False, "error": "No token provided"}, status_code=400)
    
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
        
        response = JSONResponse(content={"success": True, "user_id": user_id})
        max_age = 7 * 24 * 60 * 60  # 7 days
        secure = False  # Set to True in production
        
        response.set_cookie(key="user_id", value=user_id, max_age=max_age, httponly=True, secure=secure)
        if 'email' in decoded_token:
            response.set_cookie(key="email", value=decoded_token['email'], max_age=max_age, httponly=True, secure=secure)
        if 'name' in decoded_token:
            response.set_cookie(key="name", value=decoded_token['name'], max_age=max_age, httponly=True, secure=secure)
        
        return response
    
    except Exception as e:
        return JSONResponse(content={"success": False, "error": "Invalid token"}, status_code=401)

@app.post("/create-user")
async def create_user(request: Request):
    data = await request.json()
    uid = data.get("uid")
    email = data.get("email")
    name = data.get("name", "")
    
    if not uid or not email:
        return JSONResponse(content={"success": False, "error": "User ID and email are required"}, status_code=400)
    
    try:
        user_data = {
            'email': email,
            'name': name,
            'created_at': datetime.now()
        }
        db.collection('users').document(uid).set(user_data)
        return JSONResponse(content={"success": True})
    
    except Exception as e:
        return JSONResponse(content={"success": False, "error": "Failed to create user"}, status_code=500)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: str = Depends(verify_user)):
    user_doc = db.collection('users').document(user_id).get()
    
    if not user_doc.exists:
        user_data = {
            'email': request.cookies.get("email", ""),
            'name': request.cookies.get("name", ""),
            'created_at': datetime.now()
        }
        db.collection('users').document(user_id).set(user_data)
    
    user_boards = db.collection('boards').where('creator_id', '==', user_id).stream()
    member_boards = db.collection('boards').where('members', 'array_contains', user_id).stream()
    
    boards_data = []
    for board in user_boards:
        board_data = board.to_dict()
        board_data['id'] = board.id
        board_data['is_creator'] = True
        if 'created_at' in board_data:
            board_data['created_at'] = board_data['created_at'].replace(tzinfo=None)
        boards_data.append(board_data)
    
    for board in member_boards:
        if board.id not in [b['id'] for b in boards_data]:
            board_data = board.to_dict()
            board_data['id'] = board.id
            board_data['is_creator'] = False
            if 'created_at' in board_data:
                board_data['created_at'] = board_data['created_at'].replace(tzinfo=None)
            boards_data.append(board_data)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "boards": boards_data,
    })

@app.post("/create-board")
async def create_board(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_name = data.get("board_name", "").strip()
    
    if not board_name:
        raise HTTPException(status_code=400, detail="Please provide a board name")
    
    board_data = {
        'name': board_name,
        'creator_id': user_id,
        'members': [],
        'invited_users': [user_id],
        'created_at': datetime.now()
    }
    
    board_ref = db.collection('boards').document()
    board_ref.set(board_data)
    
    return {"success": True, "board_id": board_ref.id}

@app.get("/board/{board_id}", response_class=HTMLResponse)
async def view_board(request: Request, board_id: str, user_id: str = Depends(verify_user)):
    board_doc = db.collection('boards').document(board_id).get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id'] and user_id not in board_data.get('members', []):
        raise HTTPException(status_code=403, detail="You don't have permission to access this board")
    
    tasks = db.collection('boards').document(board_id).collection('tasks').stream()
    tasks_data = []
    for task in tasks:
        task_data = task.to_dict()
        task_data['id'] = task.id
        # Handle due_date: convert datetime to ISO string, leave string as-is
        if task_data.get('due_date'):
            if isinstance(task_data['due_date'], datetime):
                task_data['due_date'] = task_data['due_date'].isoformat()
            # If it's a string, assume it's already in a valid format
        # Handle completed_at similarly
        if task_data.get('completed_at'):
            if isinstance(task_data['completed_at'], datetime):
                task_data['completed_at'] = task_data['completed_at'].isoformat()
        assigned_users = []
        for assign_id in task_data.get('assigned_to', []):
            user_doc = db.collection('users').document(assign_id).get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                user_data['id'] = assign_id
                assigned_users.append(user_data)
        task_data['assigned_users'] = assigned_users
        tasks_data.append(task_data)
    
    all_users = []
    if board_data['creator_id'] == user_id:
        users = db.collection('users').stream()
        for user in users:
            user_data = user.to_dict()
            user_data['id'] = user.id
            all_users.append(user_data)
    
    members = []
    member_ids = board_data.get('members', []) + [board_data['creator_id']]
    for member_id in set(member_ids):
        member_doc = db.collection('users').document(member_id).get()
        if member_doc.exists:
            member_data = member_doc.to_dict()
            member_data['id'] = member_id
            members.append(member_data)
    
    total_tasks = len(tasks_data)
    completed_tasks = len([t for t in tasks_data if t.get('completed', False)])
    active_tasks = total_tasks - completed_tasks
    
    return templates.TemplateResponse("task.html", {
        "request": request, 
        "board": {**board_data, 'id': board_id},
        "tasks": tasks_data,
        "is_creator": user_id == board_data['creator_id'],
        "is_member": user_id == board_data['creator_id'] or user_id in board_data.get('members', []),
        "all_users": all_users,
        "members": members,
        "stats": {
            "total": total_tasks,
            "active": active_tasks,
            "completed": completed_tasks
        }
    })

@app.get("/board/{board_id}/members", response_class=JSONResponse)
async def get_board_members(board_id: str, user_id: str = Depends(verify_user)):
    board_doc = db.collection('boards').document(board_id).get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id']:
        raise HTTPException(status_code=403, detail="Only the board creator can view members")
    
    members = []
    member_ids = board_data.get('members', []) + [board_data['creator_id']]
    for member_id in set(member_ids):
        member_doc = db.collection('users').document(member_id).get()
        if member_doc.exists:
            member_data = member_doc.to_dict()
            member_data['id'] = member_id
            member_data['is_creator'] = member_id == board_data['creator_id']
            members.append(member_data)
    
    return {"success": True, "members": members}

@app.post("/add-task")
async def add_task(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    title = data.get("title", "").strip()
    due_date = data.get("due_date")
    assigned_to = data.get("assigned_to", [])
    
    if not title:
        raise HTTPException(status_code=400, detail="Please provide a task title")
    
    board_doc = db.collection('boards').document(board_id).get()
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    if user_id != board_data['creator_id'] and user_id not in board_data.get('members', []):
        raise HTTPException(status_code=403, detail="You don't have permission to add tasks to this board")
    
    existing_tasks = db.collection('boards').document(board_id).collection('tasks').where('title', '==', title).stream()
    if any(task.exists for task in existing_tasks):
        raise HTTPException(status_code=400, detail="A task with this name already exists")
    
    valid_assignees = board_data.get('members', []) + [board_data['creator_id']]
    invalid_assignees = [uid for uid in assigned_to if uid not in valid_assignees]
    if invalid_assignees:
        raise HTTPException(status_code=400, detail="Cannot assign task to users not in the board")
    
    task_data = {
        'title': title,
        'due_date': parse_due_date(due_date),
        'assigned_to': assigned_to,
        'completed': False,
        'created_by': user_id,
        'created_at': datetime.now(),
        'unassigned': len(assigned_to) == 0
    }
    
    task_ref = db.collection('boards').document(board_id).collection('tasks').document()
    task_ref.set(task_data)
    
    assigned_users = []
    for assign_id in assigned_to:
        user_doc = db.collection('users').document(assign_id).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            user_data['id'] = assign_id
            assigned_users.append(user_data)
    
    return {
        'success': True, 
        'task_id': task_ref.id,
        'task': task_data,
        'assigned_users': assigned_users
    }

@app.post("/update-task")
async def update_task(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    task_id = data.get("task_id")
    title = data.get("title", "").strip()
    due_date = data.get("due_date")
    assigned_to = data.get("assigned_to", [])
    
    board_doc = db.collection('boards').document(board_id).get()
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    if user_id != board_data['creator_id'] and user_id not in board_data.get('members', []):
        raise HTTPException(status_code=403, detail="You don't have permission to edit tasks")
    
    task_ref = db.collection('boards').document(board_id).collection('tasks').document(task_id)
    task_doc = task_ref.get()
    
    if not task_doc.exists:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if title:
        existing_tasks = db.collection('boards').document(board_id).collection('tasks').where('title', '==', title).stream()
        for task in existing_tasks:
            if task.id != task_id:
                raise HTTPException(status_code=400, detail="A task with this name already exists")
    
    valid_assignees = board_data.get('members', []) + [board_data['creator_id']]
    invalid_assignees = [uid for uid in assigned_to if uid not in valid_assignees]
    if invalid_assignees:
        raise HTTPException(status_code=400, detail="Cannot assign task to users not in the board")
    
    update_data = {}
    if title:
        update_data['title'] = title
    if due_date is not None:
        update_data['due_date'] = parse_due_date(due_date)
    update_data['assigned_to'] = assigned_to
    update_data['unassigned'] = len(assigned_to) == 0

    task_ref.update(update_data)
    
    return {"success": True}

@app.post("/complete-task")
async def complete_task(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    task_id = data.get("task_id")
    completed = data.get("completed", False)
    
    board_doc = db.collection('boards').document(board_id).get()
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    if user_id != board_data['creator_id'] and user_id not in board_data.get('members', []):
        raise HTTPException(status_code=403, detail="You don't have permission to modify tasks")
    
    task_ref = db.collection('boards').document(board_id).collection('tasks').document(task_id)
    
    update_data = {
        'completed': completed
    }
    
    if completed:
        update_data['completed_at'] = datetime.now()
        update_data['completed_by'] = user_id
    else:
        task_ref.update({
            'completed': False,
            'completed_at': firestore.DELETE_FIELD,
            'completed_by': firestore.DELETE_FIELD
        })
        return {"success": True}
    
    task_ref.update(update_data)
    return {"success": True}

@app.post("/delete-task")
async def delete_task(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    task_id = data.get("task_id")
    
    board_doc = db.collection('boards').document(board_id).get()
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    if user_id != board_data['creator_id'] and user_id not in board_data.get('members', []):
        raise HTTPException(status_code=403, detail="You don't have permission to delete tasks")
    
    db.collection('boards').document(board_id).collection('tasks').document(task_id).delete()
    
    return {"success": True}

@app.post("/add-member")
async def add_member(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    member_email = data.get("email", "").strip().lower()
    
    if not member_email:
        raise HTTPException(status_code=400, detail="Please provide a member email")
    
    board_ref = db.collection('boards').document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id']:
        raise HTTPException(status_code=403, detail="Only the board creator can add members")
    
    users = db.collection('users').where('email', '==', member_email).stream()
    member_user = None
    
    for user in users:
        member_user = user
        break
    
    if not member_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    member_id = member_user.id
    
    if member_id in board_data.get('members', []) or member_id == board_data['creator_id']:
        raise HTTPException(status_code=400, detail="User is already part of this board")
    
    board_ref.update({
        'members': firestore.ArrayUnion([member_id]),
        'invited_users': firestore.ArrayUnion([member_id])
    })
    
    member_data = member_user.to_dict()
    member_data['id'] = member_id
    
    return {"success": True, "member": member_data}

@app.post("/remove-member")
async def remove_member(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    member_id = data.get("member_id")
    
    board_ref = db.collection('boards').document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id']:
        raise HTTPException(status_code=403, detail="Only the board creator can remove members")
    
    if member_id == board_data['creator_id']:
        raise HTTPException(status_code=400, detail="Cannot remove the board creator")
    
    board_ref.update({
        'members': firestore.ArrayRemove([member_id]),
        'invited_users': firestore.ArrayRemove([member_id])
    })
    
    tasks = db.collection('boards').document(board_id).collection('tasks').where('assigned_to', 'array_contains', member_id).stream()
    
    for task in tasks:
        task_ref = db.collection('boards').document(board_id).collection('tasks').document(task.id)
        task_data = task.to_dict()
        
        assigned_to = task_data.get('assigned_to', [])
        if member_id in assigned_to:
            assigned_to.remove(member_id)
        
        task_ref.update({
            'assigned_to': assigned_to,
            'unassigned': len(assigned_to) == 0
        })
    
    return {"success": True}

@app.post("/rename-board")
async def rename_board(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    new_name = data.get("name", "").strip()
    
    if not new_name:
        raise HTTPException(status_code=400, detail="Please provide a new board name")
    
    board_ref = db.collection('boards').document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id']:
        raise HTTPException(status_code=403, detail="Only the board creator can rename the board")
    
    board_ref.update({
        'name': new_name
    })
    
    return {"success": True}

@app.post("/delete-board")
async def delete_board(request: Request, user_id: str = Depends(verify_user)):
    data = await request.json()
    board_id = data.get("board_id")
    
    board_ref = db.collection('boards').document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        raise HTTPException(status_code=404, detail="Board not found")
    
    board_data = board_doc.to_dict()
    
    if user_id != board_data['creator_id']:
        raise HTTPException(status_code=403, detail="Only the board creator can delete the board")
    
    if board_data.get('members', []):
        raise HTTPException(status_code=400, detail="Please remove all members before deleting the board")
    
    tasks = db.collection('boards').document(board_id).collection('tasks').limit(1).stream()
    if any(task.exists for task in tasks):
        raise HTTPException(status_code=400, detail="Please remove all tasks before deleting the board")
    
    board_ref.delete()
    
    return {"success": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)