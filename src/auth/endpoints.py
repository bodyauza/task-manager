from fastapi import Depends, APIRouter
from src.auth.auth_config import (auth_backend, current_user,
                                  get_access_strategy)

from fastapi import Response, status
from fastapi.responses import JSONResponse
from fastapi_users import models

# Добавляем новые маршруты для работы с токенами
auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

@auth_router.post("/access-token")
async def get_access_token(
        response: Response,
        user: models.UP = Depends(current_user)
):
    # Генерируем access токен
    access_token = await auth_backend.login(strategy=get_access_strategy(), user=user)
    await auth_backend.transport.get_login_response(access_token, response)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Access token successfully updated!"},
    )


@auth_router.post("/logout")
async def logout(
        response: Response,
        user: models.UP = Depends(current_user),
):
    # Удаляем куки с токеном
    await auth_backend.transport.get_logout_response(response)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Successfully logged out"},
    )