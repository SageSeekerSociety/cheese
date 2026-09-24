import type { PublicKeyCredentialCreationOptionsJSON } from '@simplewebauthn/browser'
import type { RouteLocationRaw } from 'vue-router'

import { platformAuthenticatorIsAvailable, startRegistration } from '@simplewebauthn/browser'

import { UserApi } from '@/network/api/users'

// The server's registration challenge and the sign-in ticket both last five
// minutes from sign-in; past this, a registration started with them would be
// refused after the person had already gone through the browser's dialog.
const USABLE_FOR_MS = 4 * 60 * 1000

// How long a sign-in bound for the home page waits on the password manager
// before offering a passkey itself. A password manager answers at once; the
// bound is for one that does not, so that signing in never hangs on it.
const QUIET_ATTEMPT_WAIT_MS = 1500

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
  /** The site the passkey is for, once the server has said. */
  rpId: string | undefined

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
      ({ data }) => {
        this.rpId = data.options.rp?.id
        return data.options
      },
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
 * Ask the password manager that filled the password to also keep a passkey.
 * It does so without a dialog or declines without one; either way nothing is
 * shown here. Resolves to whether a passkey was added.
 */
async function upgradeQuietly(enrollment: Enrollment): Promise<boolean> {
  if (!(await canCreateQuietly())) return false
  try {
    await enrollment.register(true)
    return true
  } catch (error) {
    console.debug('passkey: no automatic passkey after sign-in', error)
    return false
  }
}

async function deviceCanUnlockPasskeys(): Promise<boolean> {
  try {
    return await platformAuthenticatorIsAvailable()
  } catch {
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

/** What a password sign-in started for passkeys as it finished. */
export interface PasswordSignInUpgrade {
  enrollment: Enrollment
  /** Whether the password manager added a passkey without asking. */
  created: Promise<boolean>
  /** Whether the account is due the offer: no passkey, and not declined lately. */
  due: boolean
  canStopAsking: boolean
}

/** Start the quiet attempt the moment a password sign-in finishes. */
export function upgradeAfterPasswordSignIn(
  userId: number,
  passkeyEnrollment: UserApi.PasskeyEnrollment | undefined
): PasswordSignInUpgrade | null {
  if (!passkeyEnrollment) return null
  const enrollment = new Enrollment(userId, passkeyEnrollment.ticket)
  return {
    enrollment,
    created: upgradeQuietly(enrollment),
    due: passkeyEnrollment.offer,
    canStopAsking: passkeyEnrollment.canStopAsking,
  }
}

/** The offer on screen now, held in memory only: the ticket inside it must
 *  never reach a URL. */
export interface PasskeyOffer {
  enrollment: Enrollment
  created: Promise<boolean>
  canStopAsking: boolean
}

let offer: PasskeyOffer | null = null

export function currentPasskeyOffer(): PasskeyOffer | null {
  return offer
}

export function endPasskeyOffer(): void {
  offer = null
}

/**
 * Where a sign-in goes: to `target`, or, after a password, first to the
 * screen offering a passkey. The screen is shown only to someone bound for
 * the home page —
 * whoever is headed for a particular page (a link they followed, a device to
 * approve, a consent to give) is taken straight there — whose account is due
 * it, whose device can unlock a passkey with a fingerprint, face or screen
 * lock, and whose password manager did not just add one.
 */
export async function landingAfterSignIn(
  upgrade: PasswordSignInUpgrade | null,
  target: string
): Promise<RouteLocationRaw> {
  offer = null
  if (!upgrade || !upgrade.due || target !== '/') return target
  if (!(await deviceCanUnlockPasskeys())) return target
  const created = await Promise.race([
    upgrade.created,
    new Promise<false>((resolve) => setTimeout(resolve, QUIET_ATTEMPT_WAIT_MS, false)),
  ])
  if (created) return target
  offer = { enrollment: upgrade.enrollment, created: upgrade.created, canStopAsking: upgrade.canStopAsking }
  return { name: 'PasskeyOffer' }
}
