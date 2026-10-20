"""Independent executor compatibility import of the shared semantic authority."""
from docassemble.base.managed_preparation import (
    Authorizer, Claim, GENERATIONS, Preparation, PreparationError,
    RegistryRequirement, digest, parse_registry_requirement,
)

__all__ = ['Authorizer', 'Claim', 'GENERATIONS', 'Preparation', 'PreparationError',
           'RegistryRequirement', 'digest', 'parse_registry_requirement']
