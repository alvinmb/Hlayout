package app.freerouting.rules.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class HybridRulesTest {

  @Test
  void defaultsMatchThePlan() {
    HybridRules rules = HybridRules.DEFAULT;
    assertEquals(150, rules.conductor().minLineUm());
    assertEquals(150, rules.conductor().minSpaceUm());
    assertEquals(250, rules.conductor().edgeClearanceUm());
    assertEquals(500, rules.bondWire().minLengthUm());
    assertEquals(3000, rules.bondWire().maxLengthUm());
    assertEquals(45.0, rules.bondWire().maxAngleDeg(), 1e-9);
    assertFalse(rules.bondWire().crossingsAllowed());
    assertEquals(150, rules.jumper().minLoopHeightUm());
    assertEquals(List.of(), rules.problems());
  }

  @Test
  void partialRuleSetsKeepDefaultsForTheRest() {
    HybridRules rules =
        new HybridRules(new ConductorRules(null, 125, null, null, null), null, null);
    assertEquals(125, rules.conductor().minSpaceUm());
    assertEquals(150, rules.conductor().minLineUm());
    assertEquals(BondWireRules.DEFAULT, rules.bondWire());
    assertEquals(JumperRules.DEFAULT, rules.jumper());
  }

  @Test
  void inconsistentValuesAreReported() {
    HybridRules rules =
        new HybridRules(
            new ConductorRules(0, null, -1, null, null),
            new BondWireRules(3000, 500, 120.0, -1.0, null, null, null, null, 2000, 1000),
            new JumperRules(4000, 3000, null));
    List<String> problems = rules.problems();

    assertTrue(problems.contains("conductor.minLineUm must be positive"));
    assertTrue(problems.contains("conductor.edgeClearanceUm must not be negative"));
    assertTrue(problems.contains("bondWire length: minimum 3000 exceeds maximum 500"));
    assertTrue(problems.contains("bondWire.maxAngleDeg must be in (0, 90]"));
    assertTrue(problems.contains("bondWire.spacingDiameters must not be negative"));
    assertTrue(problems.contains("bondWire die-to-finger: minimum 2000 exceeds maximum 1000"));
    assertTrue(problems.contains("jumper length: minimum 4000 exceeds maximum 3000"));
  }
}
