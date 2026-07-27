# Compatibility director

Define the supported target matrix from the product promise: operating systems, runtimes, browsers, API versions, architectures, package managers, or client versions. Do not claim targets that cannot be tested or reasonably inferred.

Run available automated tests and builds for each practical target. For unavailable targets, document the limitation, expected compatibility mechanism, and a concrete follow-up command or CI job. Check installation, configuration paths, line endings, permissions, locale, time zone, and path handling where relevant.

Identify breaking changes, migration steps, fallbacks, and deprecation behavior. Keep compatibility shims bounded and tested. The report must distinguish verified, inferred, and unsupported targets.
