import type { PublicKeyCredentialCreationOptionsJSON } from '@simplewebauthn/browser'

import { startRegistration } from '@simplewebauthn/browser'

import { UserApi } from '@/network/api/users'

// The server's registration challenge and the sign-in ticket both last five
// minutes from sign-in; past this, a registration started with them would be
// refused after the person had already gone through the browser's dialog.
const USABLE_FOR_MS = 4 * 60 * 1000

/**
 * The ticket a finished password sign-in hands back, turned into one set of
 * registration options that everything after the sign-in shares. The ticket
 * is single-use, so the quiet attempt and anything that follows it must not
 * each spend their own.
 */
export class Enrollment {
  private options: Promise<PublicKeyCredentialCreationOptionsJSON> | null = null
  private spent = false
  private readonly signedInAt = Date.now()

  constructor(
    readonly userId: number,
    private readonly ticket: string
  ) {}

  /** Whether `register` can still succeed without verifying again. */
  get usable(): boolean {
    return !this.spent && Date.now() - this.signedInAt < USABLE_FOR_MS
  }

  /** Create a passkey and add it to the account. `quietly` lets only the
   *  password manager that just filled the password do it, with no dialog. */
  async register(quietly = false): Promise<void> {
    this.options ??= UserApi.getPasskeyRegistrationOptions(this.userId, this.ticket).then(
      ({ data }) => data.options,
      (error) => {
        this.spent = true
        throw error
      }
    )
    const attestation = await startRegistration({ optionsJSON: await this.options, useAutoRegister: quietly })
    // Checking a credential uses up its challenge whatever the outcome.
    this.spent = true
    await UserApi.verifyPasskeyRegistration(this.userId, attestation)
  }
}

// A browser that does not know conditional create ignores `mediation` and
// shows its full dialog instead, which here would be a dialog nobody asked
// for. So only a browser that says it can is asked.
async function canCreateQuietly(): Promise<boolean> {
  try {
    if (typeof PublicKeyCredential === 'undefined' || !('getClientCapabilities' in PublicKeyCredential)) return false
    const capabilities = await PublicKeyCredential.getClientCapabilities()
    return capabilities.conditionalCreate === true
  } catch {
    return false
  }
}

/**
 * Right after a password sign-in, ask the password manager that filled the
 * password to also keep a passkey. It does so without a dialog or declines
 * without one; either way nothing is shown here. Resolves to whether a
 * passkey was added.
 */
export async function upgradeQuietly(enrollment: Enrollment): Promise<boolean> {
  if (!(await canCreateQuietly())) return false
  try {
    await enrollment.register(true)
    return true
  } catch (error) {
    console.debug('passkey: no automatic passkey after sign-in', error)
    return false
  }
}

// The second-step page is reached both from the password form and from a
// provider's redirect; only the first follows a password, and only that one
// happens without leaving the page.
let passwordAwaitingSecondStep = false

export function passwordAccepted(): void {
  passwordAwaitingSecondStep = true
}

/** Whether the second step just completed followed a password. */
export function takePasswordStep(): boolean {
  const followed = passwordAwaitingSecondStep
  passwordAwaitingSecondStep = false
  return followed
}

/** Whatever a password sign-in leads to for passkeys, started as it finishes. */
export function afterPasswordSignIn(userId: number, passkeyEnrollment: UserApi.PasskeyEnrollment | undefined): void {
  if (!passkeyEnrollment) return
  void upgradeQuietly(new Enrollment(userId, passkeyEnrollment.ticket))
}
