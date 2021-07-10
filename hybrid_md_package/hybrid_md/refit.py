#  Hybrid MD decision making package
#
#  Copyright (c) Tamas K. Stenczel 2021.
"""
Refitting of model on the fly

This is a generic refitting function, specific ones and tweaks of
this one with the same interface are to be implemented here.
"""

import importlib
import os.path
import shutil
import subprocess
from time import time

import ase.io
import numpy as np
from hybrid_md.state_objects import HybridMD


def refit(state: HybridMD):
    """Refit a GAP model, with in-place update

    This is a generic one, which can import the functio

    Parameters
    ----------
    state: HybridMD

    """
    if state.refit_function_name is None:
        return refit_generic(state, None, None)
    else:
        refit_function_import = state.refit_function_name

        # separate import path
        module_name = ".".join(refit_function_import.split(".")[:-1])
        function_name = refit_function_import.split(".")[-1]

        # import the module of the refit function
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            raise RuntimeError(f"Refit function's module not found: {module_name}")

        # class of the calculator
        if hasattr(module, function_name):
            refit_function = getattr(module, function_name)
            assert callable(refit_function)
        else:
            raise RuntimeError(
                f"Refit function ({function_name}) not found in module {module_name}"
            )

        # YAY, all great now
        return refit_function(state)


def refit_turbo_si_c(state: HybridMD):
    # Refit function with TurboSOAP for the SiC example

    frames_train = ase.io.read(state.xyz_filename, ":") + state.get_previous_data()
    delta = np.std([at.info["QM_energy"] / len(at) for at in frames_train]) / 4

    # descriptors
    desc_str_2b = (
        f"distance_Nb order=2 n_sparse=20 cutoff=4.5 cutoff_transition_width=1.0 "
        f"compact_clusters covariance_type=ard_se theta_uniform=1.0 sparse_method=uniform "
        f"f0=0.0 add_species=T delta={delta}"
    )

    # turbo SOAP
    soap_n_sparse = 200
    soap_common = (
        " n_species=2 species_Z={{6 14}} rcut_hard=4.5 rcut_soft=3.5 alpha_max={{10 10}} l_max=6 "
        "atom_sigma_r={{0.3 0.3}} atom_sigma_t={{0.3 0.3}} atom_sigma_r_scaling={{0.10 0.10}} "
        "atom_sigma_t_scaling={{0.10 0.10}} amplitude_scaling={{1. 1.}} radial_enhancement=1 "
        "basis=poly3gauss scaling_mode=polynomial central_weight={{1. 1.}} f0=0.0 "
        "covariance_type=dot_product zeta=4 sparse_method=cur_points add_species=F "
    )
    desc_str_soap = (
        f"soap_turbo central_index=1 n_sparse={soap_n_sparse} delta={delta} {soap_common} : "
        f"soap_turbo central_index=2 n_sparse={soap_n_sparse} delta={delta} {soap_common} "
    )

    descriptor_strs = desc_str_2b + " : " + desc_str_soap

    # use lower kernel regularisation
    default_sigma = "0.005 0.050 0.1 1.0"

    return refit_generic(state, descriptor_strs, default_sigma)


def refit_generic(
    state: HybridMD, descriptor_strs: str = None, default_sigma: str = None
):
    """Refit a GAP model, with in-place update

    This is a generic very simple solution, that should work as a
    first try starting from scratch. Change this function to your
    own system and fitting settings as needed.

    Parameters
    ----------
    state: HybridMD
    descriptor_strs : str
        descriptor strings, ':' separated, no brackets around them
    default_sigma : str
        default sigma, four numbers separated by ':'

    """
    if default_sigma is None:
        default_sigma = "0.005 0.050 0.1 1.0"

    # 2B + SOAP model
    gp_name = "GAP.xml"
    frames_train = ase.io.read(state.xyz_filename, ":") + state.get_previous_data()

    if descriptor_strs is None:
        # generic 2B+SOAP, need the frames for delta
        delta = np.std([at.info["QM_energy"] / len(at) for at in frames_train]) / 4
        desc_str_2b = (
            f"distance_Nb order=2 n_sparse=20 cutoff=4.5 cutoff_transition_width=1.0 "
            f"compact_clusters covariance_type=ard_se theta_uniform=1.0 sparse_method=uniform "
            f"f0=0.0 add_species=T delta={delta}"
        )
        desc_str_soap = (
            f"soap n_sparse=200 n_max=8 l_max=4 cutoff=4.0 cutoff_transition_width=1.0 "
            f"atom_sigma=0.5 add_species=True "
            f"delta={delta} covariance_type=dot_product zeta=4 sparse_method=cur_points"
        )
        descriptor_strs = desc_str_2b + " : " + desc_str_soap

    # save the previous model
    if os.path.isfile(gp_name):
        shutil.move(gp_name, f"save__{time()}__{gp_name}")

    # training structures & delta
    ase.io.write("train.xyz", frames_train)

    # assemble the fitting string
    fit_str = (
        f"gap_fit at_file=train.xyz gp_file={gp_name} "
        f"energy_parameter_name=QM_energy force_parameter_name=QM_forces"
        f" virial_parameter_name=QM_virial "
        f"sparse_jitter=1.0e-8 do_copy_at_file=F sparse_separate_file=F "
        f"default_sigma={{ {default_sigma} }} e0_method=average "
        f"gap={{ {descriptor_strs} }}"
    )

    # fit the 2b+SOAP model
    proc = subprocess.run(
        fit_str, shell=True, capture_output=True, text=True, check=True
    )

    # print the outputs to file
    with open(f"stdout_{gp_name}_at_{time()}__.txt", "w") as file:
        file.write(proc.stdout)
    with open(f"stderr_{gp_name}_at_{time()}__.txt", "w") as file:
        file.write(proc.stderr)
