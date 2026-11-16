"""Role-based access control for views (Objective 3, F-01)."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Role


def role_required(role):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.role != role:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


student_required = role_required(Role.STUDENT)
operator_required = role_required(Role.OPERATOR)
admin_required = role_required(Role.ADMIN)
