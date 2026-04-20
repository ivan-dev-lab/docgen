const fs = require("fs");

export class Logger {
  log(message) {
    return `[log] ${message}`;
  }
}

/**
 * Create a logger instance.
 */
export const createLogger = () => {
  return new Logger();
};
