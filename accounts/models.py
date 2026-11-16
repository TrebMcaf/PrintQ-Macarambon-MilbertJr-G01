from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    OPERATOR = "OPERATOR", "Operator"
    ADMIN = "ADMIN", "Admin"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """SDD table `users`. Django's `password` field is stored in column `password_hash`."""

    user_id = models.AutoField(primary_key=True)
    email = models.EmailField(max_length=120, unique=True)  # BR-01
    password = models.CharField(max_length=255, db_column="password_hash")
    full_name = models.CharField(max_length=100)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Required by Django admin/auth; not in the SDD data dictionary.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "users"
        constraints = [
            models.CheckConstraint(condition=Q(role__in=Role.values), name="users_role_chk"),  # BR-02
        ]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    @property
    def is_operator(self):
        return self.role == Role.OPERATOR

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_student(self):
        return self.role == Role.STUDENT
