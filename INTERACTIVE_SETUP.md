# New Interactive Setup Features

## Overview
The `setup.sh` script now includes interactive prompts for:
1. Cloudflare tunnel token configuration
2. First admin user creation

## Feature 1: Cloudflare Token Setup

### When it runs
- During `./setup.sh start` command
- After config files are validated, before building services

### What it does
- Checks if `CLOUDFLARE_TOKEN` is set in `.env`
- If not configured, asks: "Do you want to configure Cloudflare tunnel now? (y/n)"
- If yes, prompts for the token and adds it to `.env`
- If no, skips and provides instructions for manual setup later

### Example interaction
```
--- Checking Cloudflare tunnel configuration... ---
Cloudflare tunnel token is not configured.
Do you want to configure Cloudflare tunnel now? (y/n) y
Enter your Cloudflare tunnel token: eyJhIjoiYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoifQ
Cloudflare token configured successfully!
Note: You'll need to restart services for the tunnel to start working.
```

## Feature 2: Admin User Creation

### When it runs
- During `./setup.sh start` command
- After all services are started and healthy

### What it does
- Checks if the `anpr-web` container is running
- Queries the database to see if any users exist
- If no users found, prompts to create the first admin user
- Validates password strength with the following requirements:
  - Minimum 10 characters
  - At least 1 uppercase letter
  - At least 1 number
  - At least 1 special character (!@#$%^&*(),.?:{}|<>~`)

### Example interaction
```
--- Checking for admin user... ---
No admin user found. Let's create the first admin user.
Enter admin username: admin
Enter admin password (min 10 chars, 1 uppercase, 1 number, 1 special char): 
Confirm password: 
Creating admin user...
Admin user 'admin' created successfully!
```

### Password validation
The script will reject passwords that don't meet requirements:
- Too short: "Password must be at least 10 characters long."
- No uppercase: "Password must contain at least one uppercase letter."
- No numbers: "Password must contain at least one number."
- No special chars: "Password must contain at least one special character."
- Mismatch: "Passwords do not match. Please try again."

## Manual User Management

If you skip the admin user creation or want to manage users later, you can use:

```bash
docker exec -it anpr-web python app/user_manager.py
```

This provides an interactive menu for:
1. List users
2. Add user
3. Remove user
4. Reset password
5. Exit

## Testing the New Features

To test these features:

1. Stop services: `./setup.sh stop`
2. Remove Cloudflare token from `.env` (if present)
3. Start services: `./setup.sh start`
4. Follow the interactive prompts

The script will guide you through:
- Cloudflare token setup (optional)
- Service building and startup
- Admin user creation (if no users exist)
