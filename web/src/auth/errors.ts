const friendlyMessages: Record<string, string> = {
  NotAuthorizedException: 'Incorrect email or password.',
  UserNotFoundException: 'Incorrect email or password.',
  UsernameExistsException: 'An account with this email already exists.',
  CodeMismatchException: 'That code is incorrect. Check the email and try again.',
  ExpiredCodeException: 'That code has expired. Request a new one.',
  InvalidPasswordException: 'Password does not meet the requirements.',
  LimitExceededException: 'Too many attempts. Wait a few minutes and try again.',
  TooManyRequestsException: 'Too many attempts. Wait a few minutes and try again.',
}

export function authErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return friendlyMessages[error.name] ?? error.message
  }
  return 'Something went wrong. Please try again.'
}
