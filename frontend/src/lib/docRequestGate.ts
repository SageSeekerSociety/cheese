// HTTP responses belong to a mounted document, not whichever topic is visible
// when they arrive. A write also invalidates reads issued before that write.
export interface DocRequest {
  topicId: string
  generation: number
  sequence: number
  writeEpoch: number
}

export function createDocRequestGate() {
  let topicId: string | null = null
  let generation = 0
  let sequence = 0
  let acceptedSequence = 0
  let writeEpoch = 0
  let version = 0

  function owns(request: DocRequest) {
    return request.topicId === topicId && request.generation === generation
  }

  return {
    select(id: string | null) {
      topicId = id
      generation++
      acceptedSequence = 0
      writeEpoch = 0
      version = 0
    },
    begin(id: string): DocRequest {
      return { topicId: id, generation, sequence: ++sequence, writeEpoch }
    },
    owns,
    invalidatedByWrite(request: DocRequest) {
      return owns(request) && request.writeEpoch !== writeEpoch
    },
    beginWrite(id: string): DocRequest {
      writeEpoch++
      return this.begin(id)
    },
    acceptRead(request: DocRequest, incomingVersion: number) {
      if (
        !owns(request) ||
        request.writeEpoch !== writeEpoch ||
        request.sequence < acceptedSequence ||
        incomingVersion < version
      )
        return false
      acceptedSequence = request.sequence
      version = incomingVersion
      return true
    },
    acceptWrite(request: DocRequest, incomingVersion: number) {
      if (!owns(request) || request.writeEpoch !== writeEpoch || incomingVersion < version) return false
      version = incomingVersion
      return true
    },
  }
}
