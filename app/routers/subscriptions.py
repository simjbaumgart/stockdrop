from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.database import add_subscriber

router = APIRouter()

class SubscriptionRequest(BaseModel):
    email: EmailStr

@router.post("/subscribe")
def subscribe(request: SubscriptionRequest):
    success = add_subscriber(request.email)
    if success:
        return {"message": "Successfully subscribed!"}
    else:
        # We return success even if already subscribed to avoid leaking info, 
        # or we could return a specific message. Let's be friendly.
        return {"message": "You are already subscribed!"}
