import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { sanitizeContractBrowserDiagnostic } from './reset-contract-runtime-support.mjs';

export default class ContractReporter {
  constructor() {
    this.secrets = existsSync('/fixture/pair.txt')
      ? [readFileSync('/fixture/pair.txt', 'utf8').trim()] : [];
  }
  print(value) {
    console.log(sanitizeContractBrowserDiagnostic(String(value), this.secrets));
  }
  onStdOut(chunk) { this.print(chunk); }
  onStdErr(chunk) { this.print(chunk); }
  onTestEnd(test, result) {
    this.print(`${test.title}: ${result.status}`);
    for (const error of result.errors) this.print(error.stack ?? error.message);
    for (const attachment of result.attachments) {
      if (attachment.path && attachment.contentType.startsWith('text/')) {
        writeFileSync(attachment.path, sanitizeContractBrowserDiagnostic(
          readFileSync(attachment.path, 'utf8'), this.secrets,
        ));
      }
    }
  }
  onError(error) { this.print(error.stack ?? error.message); }
  onEnd(result) { this.print(`C1 browser: ${result.status}`); }
}
