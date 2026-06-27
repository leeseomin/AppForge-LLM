# System definition of done

A project is release-ready only when the accepted scope exists in source, the documented start/build commands work in the available environment, required tests pass, the production build succeeds, likely secrets are absent, known limitations are explicit, and a new engineer can run and verify the result from the handoff material.

A deployment is not part of “done” unless the user explicitly requested and authorized a target environment. By default, OpenAppForge produces a release-ready source archive and deployment instructions rather than performing an external release.
