from fastapi import APIRouter

from app.api.routes.agents import router as agents_router
from app.api.routes.applications import router as applications_router
from app.api.routes.auth import router as auth_router
from app.api.routes.candidates import router as candidates_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.resumes import router as resumes_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(candidates_router)
api_router.include_router(resumes_router)
api_router.include_router(jobs_router)
api_router.include_router(agents_router)
api_router.include_router(applications_router)
