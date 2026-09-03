export function historyNavVisible(billingEnabled, isSignedIn) {
  return !billingEnabled || !!isSignedIn;
}
