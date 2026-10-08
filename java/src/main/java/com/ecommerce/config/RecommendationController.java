package com.ecommerce.config;

import com.ecommerce.model.RecommendationRequest;
import com.ecommerce.model.RecommendationResponse;
import com.ecommerce.orchestrator.SupervisorOrchestrator;
import com.ecommerce.service.ABTestService;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class RecommendationController {

    private final SupervisorOrchestrator orchestrator;
    private final ABTestService abTestService;

    public RecommendationController(SupervisorOrchestrator orchestrator, ABTestService abTestService) {
        this.orchestrator = orchestrator;
        this.abTestService = abTestService;
    }

    @PostMapping("/recommend")
    public RecommendationResponse recommend(@RequestBody RecommendationRequest request) {
        return orchestrator.recommend(request);
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "healthy", "language", "java");
    }

    @GetMapping("/experiments")
    public Map<String, Object> getExperiments() {
        return Map.of(
                "rec_strategy", Map.of(
                        "name", "推荐策略实验",
                        "groups", Map.of("control", "rule_based", "treatment_llm", "llm_rerank")
                )
        );
    }
}
