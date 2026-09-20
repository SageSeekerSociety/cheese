# Participant identity and access

People and agents use the same membership and role checks. Credentials establish
identity and a maximum scope; they do not grant a role.

- Shared rooms admit authenticated room members or project members.
- Private rooms admit only their own authenticated members.
- Project reads require project membership (including the owner and members of
  the owning team). Room membership alone does not grant project membership.
- Project owner/lead roles manage project members, ownership, settings and
  credentials. Room owner/admin roles manage that room's members.
- Team machine management requires team owner/admin roles. Team members may
  inspect machines. Projects without a team retain project owner/lead rules.
- The absence of credentials or a roster does not grant access. The explicit
  global development credential remains a trusted override.

These access rules do not remove the collaborative-mode requirement for human
review of agent output. That business rule remains separate from membership
and management roles.

Session credentials contain a stable acting agent and origin room. New sessions
explicitly permit project-scoped collaboration, with membership checked at each
destination. Credentials restricted to one room remain restricted. Execution
callbacks remain bound to their origin room.

Project credentials authenticate the agent of the project's root room at every
destination. Issuance does not add that agent to any roster. The issuance response
includes its handle so a manager can grant the required memberships and roles.
Revoking the project credential generation retires all earlier credentials.

## Rollout

Existing project credentials now use the root agent's actual memberships and no
longer borrow each destination's agent identity. Existing sessions retain their
issued credential scope until restarted. Room management callers must carry a
verified identity; body/query `actor` fields grant no authority. Before deployment,
verify existing agent seats and callers' credentials. This change does not add
production memberships or elevate roles.
