# Deployment Guide

## Overview

This project includes Docker Compose configuration with:

- **PostgreSQL/PostGIS**: Spatial database for STAC data
- **Traefik**: Reverse proxy with TLS/HTTPS support
- **CloudBeaver**: Web-based database management interface

## Quick Start

### 1. Configure Environment

Copy `.env.example` to `.env` and update with secure passwords:

```bash
cp .env.example .env
```

Edit `.env` and change default credentials:

```dotenv
POSTGRES_DB=stacdupes
POSTGRES_USER=stacdupes
POSTGRES_PASSWORD=your-secure-password-here
```

### 2. Start Services

```bash
docker compose up -d
```

### 3. Access Services

- **CloudBeaver**: https://localhost:8443 (accessed via Traefik with TLS)
- **PostgreSQL**: `localhost:5432` (local connections only)

## Security Considerations

### Port Binding

All services are bound to `127.0.0.1` (localhost only) by default:

- **Traefik HTTPS (8443)**: `127.0.0.1:8443:443` - Only accessible locally
- **PostgreSQL (5432)**: `127.0.0.1:5432:5432` - Only accessible locally
- **CloudBeaver**: Not directly exposed; accessed through Traefik only

### Opening Ports Safely

**Do NOT expose these ports to the network unless necessary.** If you need remote access:

#### For PostgreSQL Remote Access

To allow connections from other machines (NOT recommended for production without VPN/firewall):

```yaml
# In docker-compose.yml, change db service ports:
ports:
  - "5432:5432"  # Warning: Exposes to all interfaces
```

Alternatively, use SSH tunneling (recommended):

```bash
ssh -L 5432:localhost:5432 user@remote-host
```

#### For CloudBeaver Remote Access

To access CloudBeaver from other machines, modify Traefik configuration:

```yaml
# In docker-compose.yml, change traefik service ports:
ports:
  - "8443:443"  # Warning: Exposes to all interfaces
```

Then access at `https://your-host:8443`

### TLS/HTTPS Notes

- Traefik currently uses a self-signed certificate for local development
- Browser warnings about certificate trust are expected
- For production, configure proper SSL certificates (Let's Encrypt, etc.)

### Database Credentials

**CRITICAL**: Always use strong, unique passwords in production:

```bash
# Generate a secure password
openssl rand -base64 32
```

Never commit actual credentials to version control. Keep `.env` in `.gitignore`.

### CloudBeaver Initial Setup

On first access:

1. Create admin account
2. Add database connection using PostgreSQL credentials from `.env`
3. Host: `db` (Docker service name)
4. Port: `5432`
5. Database: Value of `POSTGRES_DB` in `.env`
6. Username: Value of `POSTGRES_USER` in `.env`
7. Password: Value of `POSTGRES_PASSWORD` in `.env`

## Stopping Services

```bash
docker compose down
```

To also remove volumes (including database data):

```bash
docker compose down -v
```

## Troubleshooting

### CloudBeaver Won't Connect to Database

Ensure `db` service is healthy:

```bash
docker compose ps
```

Check logs:

```bash
docker compose logs db
docker compose logs cloudbeaver
```

### HTTPS Certificate Warnings

Expected for self-signed certificates. To bypass in curl:

```bash
curl -k https://localhost:8443
```

### Port Already in Use

If ports 8443 or 5432 are already in use, modify `docker-compose.yml` to use different host ports:

```yaml
traefik:
  ports:
    - "127.0.0.1:9443:443"  # Changed from 8443

db:
  ports:
    - "127.0.0.1:5433:5432"  # Changed from 5432
```

## Additional Deployment Levels

For different deployment scenarios, create separate compose files:

- `docker-compose.yml` - Local development (current)
- `docker-compose.prod.yml` - Production with proper TLS certificates
- `docker-compose.minimal.yml` - Database only, no CloudBeaver/Traefik

Use with:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
