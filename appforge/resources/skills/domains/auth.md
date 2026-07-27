# Authentication and authorization

Choose the simplest identity model that meets the requirement. Store password hashes with a modern adaptive algorithm through a maintained library; never design cryptography. Use short-lived sessions or tokens, secure cookie flags, rotation where needed, and explicit logout/invalidation. Enforce authorization on the server for every object access. Test unauthenticated, wrong-role, cross-owner, expired, and replayed requests.
