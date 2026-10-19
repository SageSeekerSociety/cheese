// Team model connections and device login status.
import type { components } from '../api/openapi'

type Schemas = components['schemas']

/** Team connections and project teammate instances. */
export type OwnAgentsSettings = Schemas['OwnAgentsOut']
export type OwnAgentConnection = Schemas['OwnAgentConnectionOut']
