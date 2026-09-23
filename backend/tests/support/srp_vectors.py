"""One SRP-6a handshake computed by the frontend's `secure-remote-password`
(0.3.1), so the backend can be checked against the client it actually serves.

Inputs are fixed: ``SALT`` (with a leading zero byte), the client secret ``A_SECRET``
and the server secret ``B_SECRET``. Everything else is the library's output —
``client.deriveVerifier``, ``client.deriveSession`` and ``server.deriveSession`` —
and ``client.verifySession(A, session, M2)`` accepted the result.
"""

USERNAME = "srp_vector"
PASSWORD = "密码:Abc!😀"
SALT = "00a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f"
A_SECRET = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
B_SECRET = "7a6b5c4d3e2f10017a6b5c4d3e2f10017a6b5c4d3e2f10017a6b5c4d3e2f1001"

VERIFIER = (
    "4de103f1ba7535099fc8db57da22f0f33e0c41206d4359a5d5d94c7cdecd595f"
    "d3b3aebac557bd52b326341529f49574d1823bf78a60676e592b7bfa40878153"
    "8199971f231aaa808ba496c284ba6f019b9495b5ad5d90c9b80f2e550e1a737d"
    "7dac94b5ceff6aeb253eabb42b0dd334fbadd9339c803785b6755376669ad7e4"
    "387aa008fa1ac713f6b8a3eb2e08e22d3b15eeea22c4a4f2c2e92dc1bd9a09e1"
    "2a3399acaf3aa2c548755875192981e9dddec750367f41e3602c4349d3989242"
    "8314f96a8cbdf4256f11d98a1c5ddb6447233daa4d28aec6106bdab34c672bc4"
    "f8e2e5da4ccb9d2f78fe9aa25ce8a97c88d44d772f67fe1e00fdb21b7d684785"
)
A = (
    "2686d09e8312ed59530b3c7c0a059d4102f4d5a3ae816566b9034c26bd7dae1a"
    "f2a58009612fbabb57258ded0b69eb797fd9e3eb602c0667c398f043432f04cd"
    "fd50f11b7b03a8051233d721fa855b7257b7b66ef74763aa07e088f8d95811ee"
    "b3fa84370c1caf7d1002823b87388b43662839d2a6a402246e2ad4d10726ed53"
    "482f2f3e18699e13cf841645023fae835469c6f739c2e1fcf1a8df8e522016bd"
    "58f334a0abcab393b04e6cd0e0f5ee5130f8b83f8c241eb16b9794f2133491a9"
    "4d83043d7866a8baa7c4e9001dd1bf0729d567343f9043d782aae9335453e64a"
    "eb335a0229cb98725675e95e8dc7014d050a864365f4240157748820be643ae9"
)
B = (
    "a983f032434e10a6ed0e723e5e6631edcb9c137322af91b233d53aac82c15771"
    "231794164e4d92ca116410e909a1a920081b9d217c7699d4fb01eb75e9cd9dcd"
    "0ceb1bfdaa39dbc95db29320dff739c2d55549ad9e06c70c35a83c8e002361d5"
    "0965f2ff9cf9a330a2cc5def4f34b43223e22931e9c5050af57728383e43f3d0"
    "58c453b840ceed16dbedb8797ae7606c3aba9e4b043ea668e0a7b819ef9858da"
    "19b76ec4305e4965969abbf779d24b253217cd93f20e9e2fc2e9657613367d96"
    "f569e6fddad5d88bc7ac4fd9008a11afd2d4dd1272f86aafcafaf2da3b05e226"
    "ecbf303b6c867d28e58469b201ad9486599748eb050a7ac4d2293ed0fb4612d2"
)
M1 = "5334ca76391276f07733d17914d6f2d0f8d399cd073ea76823a238e601632df6"
M2 = "680804e706a3e2dfd4a6dd89bcceda10c506c3e1cac1c46baff7b590cf55bc78"
