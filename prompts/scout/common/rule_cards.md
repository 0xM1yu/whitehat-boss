# Fixed Rule Cards

These cards are a shared, review-oriented seed set for all v4 profiles. They are not the final evaluation set, are not Ground Truth, and are not FACTS evidence. The evaluation CWE scope is **UNCONFIRMED**; the cards must not be selected from a target label or hidden answer.

Each card separates an official weakness description from this package's short operational restatement. The sources are provided for offline documentation and are not a runtime RAG instruction.

## Card `bounds.copy_or_index`

- `rule_id`: `bounds.copy_or_index`
- `related_cwe`: `CWE-120`, `CWE-125`, `CWE-170`, `CWE-787`
- `security_rule`: A transfer or index is unsafe only when the target object's valid extent is smaller than the accessed or copied extent, or when termination requirements are not met.
- `necessary_conditions`:
  1. Identify the destination object or indexed object and its actual capacity/valid range.
  2. Identify the input length, index, count, or terminator requirement at the operation.
  3. Establish the relation between those values on an executable path.
  4. Establish that a visible defense does not enforce the required relation on that path.
- `required_code_info`: declaration/initializer or allocation size, length/index calculation, conversion, relevant guard, and the call or access operation.
- `counterevidence`: a visible bound proving `length <= capacity`, a valid index invariant, a size-aware API with the required contract, or a complete terminator-space check.
- `request_if_missing`: `missing_request:variable:<target>:buffer_capacity`, `missing_request:variable:<target>:length_bound`, `missing_request:allocation:<target>:object_extent`, or `missing_request:guard:<target>:index_invariant`.
- `basis`: The CWE descriptions are the official basis; the conditions above are a concise operational restatement for this experiment, not a verbatim official rule.
- `sources`: [CWE-120](https://cwe.mitre.org/data/definitions/120.html), [CWE-125](https://cwe.mitre.org/data/definitions/125.html), [CWE-170](https://cwe.mitre.org/data/definitions/170.html), [CWE-787](https://cwe.mitre.org/data/definitions/787.html), [CERT STR31-C](https://wiki.sei.cmu.edu/confluence/spaces/c/pages/87152048/STR31-C.%2BGuarantee%2Bthat%2Bstorage%2Bfor%2Bstrings%2Bhas%2Bsufficient%2Bspace%2Bfor%2Bcharacter%2Bdata%2Band%2Bthe%2Bnull%2Bterminator)

## Card `integer.size_calculation`

- `rule_id`: `integer.size_calculation`
- `related_cwe`: `CWE-190`, `CWE-191`, `CWE-681`
- `security_rule`: Numeric conversion or arithmetic is unsafe when its representational range or signedness changes a security-relevant size, index, loop bound, or allocation decision in a way not checked by the program.
- `necessary_conditions`:
  1. Identify operand types, widths, signedness, and the evaluation type.
  2. Identify the range of input values that reaches the operation.
  3. Show overflow, underflow, truncation, sign conversion, or precision loss for a relevant value.
  4. Show the resulting value affects a security-relevant operation or decision.
- `required_code_info`: declarations, casts/conversions, arithmetic operands, input range, comparison/guard, and the consumer of the result.
- `counterevidence`: a range check covering the actual type and operation, checked multiplication/addition, safe conversion, or a proven bounded input.
- `request_if_missing`: `missing_request:variable:<target>:signedness_width`, `missing_request:context:<target>:input_range`, `missing_request:guard:<target>:range_check`, or `missing_request:function:<target>:conversion_contract`.
- `basis`: Official CWE descriptions and CERT integer guidance are the basis; the operational conditions are reconstructed for this package.
- `sources`: [CWE-190](https://cwe.mitre.org/data/definitions/190.html), [CWE-191](https://cwe.mitre.org/data/definitions/191.html), [CWE-681](https://cwe.mitre.org/data/definitions/681.html), [CERT INT32-C](https://wiki.sei.cmu.edu/confluence/spaces/c/pages/87152210/INT32-C.%2BEnsure%2Bthat%2Boperations%2Bon%2Bsigned%2Bintegers%2Bdo%2Bnot%2Bresult%2Bin%2Boverflow)

## Card `null.initialization`

- `rule_id`: `null.initialization`
- `related_cwe`: `CWE-252`, `CWE-457`, `CWE-476`
- `security_rule`: A dereference or use is unsafe only when the value can be null or indeterminate on the reaching path and no visible check or initialization establishes a valid value before use.
- `necessary_conditions`:
  1. Identify the value definition, return, or assignment.
  2. Identify paths that can produce null or an indeterminate value.
  3. Connect that value to the use without an intervening valid check or initialization.
  4. Confirm the use is reachable under the stated conditions.
- `required_code_info`: initializer/return contract, assignments, branch guards, aliasing, and dereference/use.
- `counterevidence`: a visible non-null initialization, checked return contract, dominating null check, or a proven impossible path.
- `request_if_missing`: `missing_request:function:<target>:return_contract`, `missing_request:variable:<target>:initializer`, `missing_request:guard:<target>:null_check`, or `missing_request:context:<target>:reachability`.
- `basis`: Official CWE descriptions provide the weakness concepts; this card's path conditions are an experimental restatement.
- `sources`: [CWE-252](https://cwe.mitre.org/data/definitions/252.html), [CWE-457](https://cwe.mitre.org/data/definitions/457.html), [CWE-476](https://cwe.mitre.org/data/definitions/476.html)

## Card `lifetime.ownership`

- `rule_id`: `lifetime.ownership`
- `related_cwe`: `CWE-401`, `CWE-415`, `CWE-416`
- `security_rule`: A resource or object use is unsafe when ownership, release, or lifetime rules allow a leak, double release, or use after release on a reachable path.
- `necessary_conditions`:
  1. Identify allocation/acquisition and ownership transfer.
  2. Identify release operations and path conditions.
  3. Establish the invalid ordering, duplicate release, or missing release.
  4. Exclude visible cleanup or lifetime guarantees that repair the condition.
- `required_code_info`: allocation/free or acquire/release sites, aliases, ownership contract, branches, and use sites.
- `counterevidence`: a visible single-owner contract, dominating cleanup, reference-count/lifetime guarantee, or path proof preventing the invalid ordering.
- `request_if_missing`: `missing_request:function:<target>:ownership_contract`, `missing_request:variable:<target>:alias_relation`, `missing_request:context:<target>:cleanup_path`, or `missing_request:guard:<target>:lifetime_condition`.
- `basis`: Official CWE entries are the basis; the path checklist is a package-specific operational restatement.
- `sources`: [CWE-401](https://cwe.mitre.org/data/definitions/401.html), [CWE-415](https://cwe.mitre.org/data/definitions/415.html), [CWE-416](https://cwe.mitre.org/data/definitions/416.html)

## Card `injection.interpretation`

- `rule_id`: `injection.interpretation`
- `related_cwe`: `CWE-78`, `CWE-89`
- `security_rule`: Injection requires a data value controlled by an external actor to reach an interpreter or command/query boundary without a defense that preserves the intended data/command separation.
- `necessary_conditions`:
  1. Identify the external input and its provenance from visible code or registered evidence.
  2. Trace the value to a command/query/interpreter boundary.
  3. Establish that the boundary interprets attacker-controlled content as syntax or directives.
  4. Check escaping, parameterization, fixed argument separation, allowlisting, and other visible defenses.
- `required_code_info`: input source, transformations, sink/interpreter call, argument construction, and defense contract.
- `counterevidence`: parameterized query, fixed argument vector, context-appropriate escaping, or strict allowlist can rebut the hypothesis when the relevant contract is visible. "No visible external flow" by itself is absence of evidence, not proof that external control is excluded.
- `request_if_missing`: `missing_request:context:<target>:attacker_control`, `missing_request:function:<target>:sanitization_contract`, `missing_request:function:<target>:interpreter_boundary`, or `missing_request:context:<target>:data_flow`.
- `basis`: The official CWE descriptions are the basis; the explicit source/interpretation/defense sequence is an operational restatement.
- `sources`: [CWE-78](https://cwe.mitre.org/data/definitions/78.html), [CWE-89](https://cwe.mitre.org/data/definitions/89.html)

## Card `format.interpretation`

- `rule_id`: `format.interpretation`
- `related_cwe`: `CWE-134`
- `security_rule`: A format-string issue requires an externally influenced or otherwise unintended format string to reach a formatting interpreter; an ordinary value argument alone is not sufficient.
- `necessary_conditions`:
  1. Identify the format string expression and its origin.
  2. Confirm that the called API interprets it as a format string.
  3. Confirm attacker influence or a relevant untrusted origin.
  4. Check a constant format string, safe wrapper, or correct argument separation.
- `required_code_info`: format-string expression, API contract, input flow, wrapper implementation, and visible validation.
- `counterevidence`: literal format string with data passed as an argument, safe formatting API, or visible evidence that the format string cannot be externally influenced. Missing provenance is absence of evidence; treat it as `unknown` and request context when it is material.
- `request_if_missing`: `missing_request:context:<target>:format_string_origin`, `missing_request:function:<target>:wrapper_contract`, or `missing_request:function:<target>:format_api_contract`.
- `basis`: Official CWE and CERT format-string guidance are the basis; the conditions are a concise experimental restatement.
- `sources`: [CWE-134](https://cwe.mitre.org/data/definitions/134.html), [CERT FIO30-C](https://wiki.sei.cmu.edu/confluence/display/c/FIO30-C.+Exclude+user+input+from+format+strings)
