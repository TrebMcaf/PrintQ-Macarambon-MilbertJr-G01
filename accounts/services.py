"""Account-administration rules (single source of truth for admin edits, toggles and password resets)."""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Role, User

SUPERUSER_MSG = "Superuser accounts keep the Admin role and stay active. Manage them in Django admin."
SELF_DEACTIVATE_MSG = "You can't deactivate your own account."
LAST_ADMIN_MSG = "PrintQ needs at least one active admin. Make someone else an admin first."


def is_locked(user) -> bool:
    """Superuser accounts are managed in Django admin, not in the app."""
    return user.is_superuser


def other_active_admins(target) -> int:
    return User.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=target.pk).count()


def check_account_change(actor, target, new_role, new_active, admin_rows=None) -> None:
    """Raise ValidationError (plain message) if the change is not allowed.

    admin_rows: optional list of (pk, is_active) for all ADMIN-role users, read under a lock by
    apply_account_change. When given, rule 3 counts from it instead of querying again.
    """
    # Rule 1 (R1/R13): only an actual role/status change of a superuser is blocked.
    if is_locked(target) and (new_role != target.role or new_active != target.is_active):
        raise ValidationError(SUPERUSER_MSG)
    # Rule 2
    if target.pk == actor.pk and not new_active:
        raise ValidationError(SELF_DEACTIVATE_MSG)
    # Rule 3: an active admin is being demoted or deactivated while no other active admin exists.
    if target.role == Role.ADMIN and target.is_active and (new_role != Role.ADMIN or not new_active):
        if admin_rows is not None:
            others = sum(1 for pk, active in admin_rows if active and pk != target.pk)
        else:
            others = other_active_admins(target)
        if others == 0:
            raise ValidationError(LAST_ADMIN_MSG)


@transaction.atomic
def apply_account_change(actor, target_pk, *, role=None, is_active, **fields) -> User:
    """Serialize admin-role changes, re-check the rules, then save.

    select_for_update locks the admin rows so two concurrent demotions cannot both pass rule 3.
    Note: SQLite (used in tests) ignores select_for_update, so tests cover the logic only.
    """
    admin_rows = list(User.objects.select_for_update().filter(role=Role.ADMIN).values_list("pk", "is_active"))
    target = User.objects.select_for_update().get(pk=target_pk)  # fresh row, ORIGINAL values
    role = target.role if role is None else role
    check_account_change(actor, target, role, is_active, admin_rows=admin_rows)
    for key, value in fields.items():
        setattr(target, key, value)
    target.role, target.is_active = role, is_active
    target.save()
    return target


def can_reset_password(actor, target) -> bool:
    return not is_locked(target) or actor.pk == target.pk
