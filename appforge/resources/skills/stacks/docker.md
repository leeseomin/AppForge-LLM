# Container stack skill

Use a small multi-stage build, a non-root runtime user, pinned base image family, `.dockerignore`, health checks, and environment-driven configuration. Do not bake secrets into layers or build arguments. Keep runtime images free of compilers and caches. Document ports, volumes, migrations, and graceful shutdown. A container is a packaging boundary, not a substitute for application tests.
