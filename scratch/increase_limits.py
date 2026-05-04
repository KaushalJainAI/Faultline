import os
import django
import sys

# Add the Backend directory to the path
backend_path = r'c:\Users\91700\Desktop\AIAAS\Backend'
sys.path.append(backend_path)

# Set the Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workflow_backend.settings.local')

# Setup Django
django.setup()

from django.contrib.auth.models import User
from core.models import UserProfile

try:
    user = User.objects.get(id=3)
    print(f"Found user: {user.username} (ID: {user.id})")
    
    profile, created = UserProfile.objects.get_or_create(user=user)
    print(f"Current tier: {profile.tier}")
    
    # Update to Enterprise and increase limits
    profile.tier = 'enterprise'
    profile.compile_limit = 1000
    profile.execute_limit = 1000
    profile.stream_connections = 1000
    profile.credits_remaining = 1000000
    profile.save()
    
    print(f"Updated user {user.username} to Enterprise tier with high limits.")
except User.DoesNotExist:
    print("User with ID 3 not found.")
except Exception as e:
    print(f"Error: {e}")
