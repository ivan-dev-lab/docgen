import { add, multiply } from "./lib/math";
import { createLogger } from "./utils/logger";
import React from "react";

/**
 * Boot the sample application.
 */
export function bootstrapApp(): number {
  const logger = createLogger();
  logger.log("boot");
  return add(2, multiply(3, 4));
}

export class Application {
  run(): number {
    return bootstrapApp();
  }
}
