"""
Domain definitions for cross-domain awareness.

Each domain represents a sphere of life/activity. Goals are tagged with
one or more domains. Cross-domain conflict detection uses these definitions
to understand how goals in different domains can interfere with each other.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set, Optional


class Domain(str, Enum):
    """Core life domains. Extensible via the registry."""
    WORK = "work"
    CAREER = "career"          # distinct from work: career is trajectory, work is day-to-day
    HEALTH = "health"
    FITNESS = "fitness"
    MENTAL_HEALTH = "mental_health"
    RELATIONSHIPS = "relationships"
    FAMILY = "family"
    SOCIAL = "social"
    FINANCE = "finance"
    LEARNING = "learning"
    CREATIVE = "creative"
    SPIRITUAL = "spiritual"
    COMMUNITY = "community"
    PERSONAL_GROWTH = "personal_growth"
    HOME = "home"
    TRAVEL = "travel"
    REST = "rest"
    UNKNOWN = "unknown"


@dataclass
class DomainProfile:
    """
    Describes a domain's characteristics relevant to conflict detection.
    """
    domain: Domain
    display_name: str
    description: str

    # Resources this domain typically consumes (resource_name -> typical_intensity 0.0-1.0)
    resource_consumption: Dict[str, float] = field(default_factory=dict)

    # Resources this domain typically produces/replenishes
    resource_production: Dict[str, float] = field(default_factory=dict)

    # Domains that are naturally aligned (synergy potential)
    aligned_with: Set[Domain] = field(default_factory=set)

    # Domains that frequently create tension when both are pursued intensely
    tensions_with: Set[Domain] = field(default_factory=set)

    # Whether this domain is typically time-bound (schedule-dependent)
    time_bound: bool = False

    # Whether this domain is typically energy-dependent
    energy_dependent: bool = True

    # Default priority weight when not specified (0.0-1.0)
    default_priority: float = 0.5


# --- Shared Resources ---
# These are the finite resources that goals across all domains compete for.
SHARED_RESOURCES = {
    "time_daily",          # hours in a day
    "time_weekly",         # hours in a week
    "energy_physical",     # physical energy
    "energy_mental",       # cognitive capacity / focus
    "energy_emotional",    # emotional bandwidth
    "money",               # financial resources
    "attention",           # ability to focus on something
    "willpower",           # decision-making / self-control reserves
    "social_capital",      # goodwill / favors / relationship investment
    "space",               # physical space (home, office, etc.)
}


# --- Default Domain Profiles ---
DEFAULT_PROFILES: Dict[Domain, DomainProfile] = {
    Domain.WORK: DomainProfile(
        domain=Domain.WORK,
        display_name="Work",
        description="Day-to-day job responsibilities and deliverables.",
        resource_consumption={
            "time_daily": 0.5,
            "time_weekly": 0.4,
            "energy_mental": 0.6,
            "energy_physical": 0.3,
            "energy_emotional": 0.3,
            "attention": 0.6,
            "willpower": 0.4,
        },
        aligned_with={Domain.CAREER, Domain.LEARNING, Domain.FINANCE},
        tensions_with={Domain.REST, Domain.FAMILY, Domain.HEALTH, Domain.SOCIAL},
        time_bound=True,
        default_priority=0.7,
    ),
    Domain.CAREER: DomainProfile(
        domain=Domain.CAREER,
        display_name="Career",
        description="Long-term professional trajectory and advancement.",
        resource_consumption={
            "time_weekly": 0.2,
            "energy_mental": 0.4,
            "attention": 0.3,
            "willpower": 0.3,
        },
        resource_production={"money": 0.5, "social_capital": 0.3},
        aligned_with={Domain.WORK, Domain.LEARNING, Domain.FINANCE, Domain.PERSONAL_GROWTH},
        tensions_with={Domain.REST, Domain.FAMILY},
        time_bound=False,
        default_priority=0.6,
    ),
    Domain.HEALTH: DomainProfile(
        domain=Domain.HEALTH,
        display_name="Health",
        description="Physical and mental wellbeing, medical care, sleep.",
        resource_consumption={
            "time_daily": 0.15,
            "energy_physical": 0.2,
            "money": 0.1,
            "willpower": 0.2,
        },
        resource_production={"energy_physical": 0.5, "energy_mental": 0.3, "energy_emotional": 0.3},
        aligned_with={Domain.FITNESS, Domain.MENTAL_HEALTH, Domain.REST},
        tensions_with=set(),
        time_bound=True,
        default_priority=0.8,
    ),
    Domain.FITNESS: DomainProfile(
        domain=Domain.FITNESS,
        display_name="Fitness",
        description="Exercise, training, physical performance.",
        resource_consumption={
            "time_daily": 0.1,
            "energy_physical": 0.3,
            "willpower": 0.3,
            "money": 0.1,
        },
        resource_production={"energy_physical": 0.4, "energy_mental": 0.2},
        aligned_with={Domain.HEALTH, Domain.MENTAL_HEALTH},
        tensions_with={Domain.WORK, Domain.REST},
        time_bound=True,
        default_priority=0.6,
    ),
    Domain.MENTAL_HEALTH: DomainProfile(
        domain=Domain.MENTAL_HEALTH,
        display_name="Mental Health",
        description="Therapy, mindfulness, stress management, emotional regulation.",
        resource_consumption={
            "time_daily": 0.05,
            "energy_emotional": 0.2,
            "money": 0.05,
        },
        resource_production={"energy_emotional": 0.5, "energy_mental": 0.3, "willpower": 0.3},
        aligned_with={Domain.HEALTH, Domain.REST, Domain.SPIRITUAL, Domain.PERSONAL_GROWTH},
        tensions_with=set(),
        time_bound=False,
        default_priority=0.8,
    ),
    Domain.RELATIONSHIPS: DomainProfile(
        domain=Domain.RELATIONSHIPS,
        display_name="Relationships",
        description="Romantic partnerships, dating, close friendships.",
        resource_consumption={
            "time_weekly": 0.15,
            "energy_emotional": 0.3,
            "attention": 0.2,
            "money": 0.1,
            "social_capital": 0.2,
        },
        resource_production={"energy_emotional": 0.3, "social_capital": 0.4},
        aligned_with={Domain.FAMILY, Domain.SOCIAL},
        tensions_with={Domain.WORK, Domain.CAREER},
        time_bound=False,
        default_priority=0.7,
    ),
    Domain.FAMILY: DomainProfile(
        domain=Domain.FAMILY,
        display_name="Family",
        description="Children, parents, siblings, family obligations.",
        resource_consumption={
            "time_daily": 0.2,
            "time_weekly": 0.2,
            "energy_emotional": 0.3,
            "energy_physical": 0.2,
            "money": 0.2,
            "attention": 0.2,
        },
        resource_production={"energy_emotional": 0.3, "social_capital": 0.3},
        aligned_with={Domain.RELATIONSHIPS, Domain.COMMUNITY},
        tensions_with={Domain.WORK, Domain.CAREER, Domain.FITNESS},
        time_bound=True,
        default_priority=0.8,
    ),
    Domain.SOCIAL: DomainProfile(
        domain=Domain.SOCIAL,
        display_name="Social",
        description="Socializing, networking, community events, friendships.",
        resource_consumption={
            "time_weekly": 0.1,
            "energy_emotional": 0.2,
            "energy_physical": 0.1,
            "money": 0.1,
            "social_capital": 0.1,
        },
        resource_production={"social_capital": 0.4, "energy_emotional": 0.2},
        aligned_with={Domain.RELATIONSHIPS, Domain.COMMUNITY},
        tensions_with={Domain.WORK, Domain.REST},
        time_bound=False,
        default_priority=0.4,
    ),
    Domain.FINANCE: DomainProfile(
        domain=Domain.FINANCE,
        display_name="Finance",
        description="Saving, investing, budgeting, debt reduction.",
        resource_consumption={
            "time_weekly": 0.05,
            "energy_mental": 0.1,
            "willpower": 0.2,
            "money": -0.5,  # negative consumption = production (saving money)
        },
        resource_production={"money": 0.5},
        aligned_with={Domain.WORK, Domain.CAREER},
        tensions_with={Domain.TRAVEL, Domain.SOCIAL, Domain.CREATIVE, Domain.HOME},
        time_bound=False,
        default_priority=0.6,
    ),
    Domain.LEARNING: DomainProfile(
        domain=Domain.LEARNING,
        display_name="Learning",
        description="Skill acquisition, courses, reading, study.",
        resource_consumption={
            "time_weekly": 0.15,
            "energy_mental": 0.4,
            "attention": 0.3,
            "willpower": 0.3,
            "money": 0.1,
        },
        resource_production={"energy_mental": 0.1},  # meta: learning makes future learning easier
        aligned_with={Domain.CAREER, Domain.PERSONAL_GROWTH, Domain.WORK},
        tensions_with={Domain.REST, Domain.SOCIAL, Domain.FAMILY},
        time_bound=False,
        default_priority=0.5,
    ),
    Domain.CREATIVE: DomainProfile(
        domain=Domain.CREATIVE,
        display_name="Creative",
        description="Art, writing, music, making, side projects.",
        resource_consumption={
            "time_weekly": 0.1,
            "energy_mental": 0.3,
            "energy_emotional": 0.2,
            "attention": 0.3,
            "money": 0.1,
        },
        resource_production={"energy_emotional": 0.2, "energy_mental": 0.1},
        aligned_with={Domain.PERSONAL_GROWTH, Domain.LEARNING},
        tensions_with={Domain.WORK, Domain.FINANCE},
        time_bound=False,
        default_priority=0.4,
    ),
    Domain.SPIRITUAL: DomainProfile(
        domain=Domain.SPIRITUAL,
        display_name="Spiritual",
        description="Meditation, religious practice, contemplation, meaning-making.",
        resource_consumption={
            "time_daily": 0.05,
            "attention": 0.1,
        },
        resource_production={"energy_emotional": 0.3, "willpower": 0.2},
        aligned_with={Domain.MENTAL_HEALTH, Domain.REST, Domain.PERSONAL_GROWTH},
        tensions_with=set(),
        time_bound=False,
        default_priority=0.4,
    ),
    Domain.COMMUNITY: DomainProfile(
        domain=Domain.COMMUNITY,
        display_name="Community",
        description="Volunteering, civic engagement, neighborhood involvement.",
        resource_consumption={
            "time_weekly": 0.1,
            "energy_emotional": 0.2,
            "social_capital": 0.2,
        },
        resource_production={"social_capital": 0.5, "energy_emotional": 0.2},
        aligned_with={Domain.SOCIAL, Domain.FAMILY, Domain.SPIRITUAL},
        tensions_with={Domain.WORK, Domain.CAREER},
        time_bound=False,
        default_priority=0.3,
    ),
    Domain.PERSONAL_GROWTH: DomainProfile(
        domain=Domain.PERSONAL_GROWTH,
        display_name="Personal Growth",
        description="Self-improvement, journaling, coaching, reflection.",
        resource_consumption={
            "time_weekly": 0.05,
            "energy_mental": 0.2,
            "energy_emotional": 0.2,
            "willpower": 0.2,
        },
        resource_production={"willpower": 0.2, "energy_emotional": 0.2},
        aligned_with={Domain.LEARNING, Domain.MENTAL_HEALTH, Domain.SPIRITUAL, Domain.CREATIVE},
        tensions_with=set(),
        time_bound=False,
        default_priority=0.5,
    ),
    Domain.HOME: DomainProfile(
        domain=Domain.HOME,
        display_name="Home",
        description="Household management, chores, living space, maintenance.",
        resource_consumption={
            "time_weekly": 0.1,
            "energy_physical": 0.2,
            "money": 0.2,
        },
        aligned_with={Domain.FAMILY, Domain.FINANCE},
        tensions_with={Domain.WORK, Domain.CAREER, Domain.TRAVEL},
        time_bound=True,
        default_priority=0.5,
    ),
    Domain.TRAVEL: DomainProfile(
        domain=Domain.TRAVEL,
        display_name="Travel",
        description="Trips, vacations, visits, exploration.",
        resource_consumption={
            "money": 0.3,
            "time_weekly": 0.1,
            "energy_physical": 0.2,
            "energy_emotional": 0.1,
        },
        resource_production={"energy_emotional": 0.3, "energy_mental": 0.2},
        aligned_with={Domain.RELATIONSHIPS, Domain.SOCIAL, Domain.PERSONAL_GROWTH},
        tensions_with={Domain.WORK, Domain.FINANCE, Domain.HOME, Domain.FITNESS},
        time_bound=True,
        default_priority=0.3,
    ),
    Domain.REST: DomainProfile(
        domain=Domain.REST,
        display_name="Rest",
        description="Sleep, downtime, recovery, leisure, decompression.",
        resource_consumption={
            "time_daily": 0.3,
        },
        resource_production={
            "energy_physical": 0.6,
            "energy_mental": 0.5,
            "energy_emotional": 0.4,
            "willpower": 0.5,
        },
        aligned_with={Domain.HEALTH, Domain.MENTAL_HEALTH},
        tensions_with={Domain.WORK, Domain.CAREER, Domain.LEARNING, Domain.SOCIAL},
        time_bound=True,
        default_priority=0.7,
    ),
    Domain.UNKNOWN: DomainProfile(
        domain=Domain.UNKNOWN,
        display_name="Unknown",
        description="Unclassified domain.",
        resource_consumption={"time_daily": 0.1, "energy_mental": 0.1},
        aligned_with=set(),
        tensions_with=set(),
        time_bound=False,
        default_priority=0.5,
    ),
}


class DomainRegistry:
    """
    Registry for domain profiles. Allows extension with custom domains
    and modification of default profiles at runtime.
    """

    def __init__(self, profiles: Optional[Dict[Domain, DomainProfile]] = None):
        self._profiles: Dict[Domain, DomainProfile] = {}
        # Load defaults
        for domain, profile in (profiles or DEFAULT_PROFILES).items():
            self._profiles[domain] = profile

    def get(self, domain: Domain) -> DomainProfile:
        return self._profiles.get(domain, self._profiles[Domain.UNKNOWN])

    def register(self, profile: DomainProfile) -> None:
        """Register or replace a domain profile."""
        self._profiles[profile.domain] = profile

    def all_domains(self) -> List[Domain]:
        return list(self._profiles.keys())

    def get_tensions(self, domain: Domain) -> Set[Domain]:
        """Returns domains that have tension with the given domain."""
        profile = self.get(domain)
        tensions = set(profile.tensions_with)
        # Tension is bidirectional — check if other domains list this one
        for other_domain, other_profile in self._profiles.items():
            if domain in other_profile.tensions_with:
                tensions.add(other_domain)
        tensions.discard(domain)  # don't include self
        return tensions

    def get_alignments(self, domain: Domain) -> Set[Domain]:
        """Returns domains aligned with the given domain."""
        profile = self.get(domain)
        alignments = set(profile.aligned_with)
        for other_domain, other_profile in self._profiles.items():
            if domain in other_profile.aligned_with:
                alignments.add(other_domain)
        alignments.discard(domain)
        return alignments

    def get_shared_resources(self, domain_a: Domain, domain_b: Domain) -> Set[str]:
        """
        Returns the set of shared resources that both domains consume.
        These are the resources where cross-domain competition can occur.
        """
        profile_a = self.get(domain_a)
        profile_b = self.get(domain_b)
        resources_a = set(profile_a.resource_consumption.keys())
        resources_b = set(profile_b.resource_consumption.keys())
        return resources_a & resources_b & SHARED_RESOURCES
