# Packaging director

Build the desktop deliverable using the repository's packaging system. Separate application build success from platform signing, notarization, installer generation, and store submission.

Identify produced packages, target operating systems and architectures, bundled runtime, update behavior, configuration location, data directory, install/uninstall steps, and known platform caveats. Verify the package does not embed secrets, development endpoints, or writable executable content.

When the current machine cannot build another platform, keep the configuration and CI recipe correct, test the available target, and report the unverified target honestly. Never invent a signed or notarized artifact.
