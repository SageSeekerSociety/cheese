# Private chat scratch execution

Goal: run private chats through central Claude Code sessions with a small isolated shell workspace. Published documents remain in platform storage.

Architecture: a configured central device hosts the Claude Code and RC sessions. Each private chat uses a separate container with a read-only image and 64 MiB of writable temporary storage. The existing execution proxy sends Bash and file operations into that container. No project checkout, host directory, Docker socket, or model credential is mounted into it. Drafts survive turns while the container lives; releasing the chat removes the container. Recreating a lost container starts with empty scratch space.

Implementation:
1. Add the private executor image and lifecycle helper beside remote_execution. Reuse the native file engine and Cheese CLI. Test shell execution, native file operations, disk exhaustion, host isolation, and recreation.
2. Route private chats to the configured central device and remove the model path without tools. Keep ordinary work topics on their selected compute provider.
3. Route private RC file and task controls to the executor. Keep the model transcript and RC session on the central device. Exclude hooks and skills written in scratch space from central context.
4. Verify document publication, draft reuse across turns, archive cleanup, and failure behavior. Run the existing execution and device regression tests.

The work remains on the experimental branch. It is not deployed or merged. Container isolation limits filesystem and process access; API authorization continues to govern Cheese operations. General outbound network access remains available for file-processing tools.
