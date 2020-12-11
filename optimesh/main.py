import re

import meshplex
import numpy

from . import cpt, cvt, laplace, odt
from .helpers import print_stats

methods = {
    "lloyd": cvt.lloyd,
    "cvt-diaognal": cvt.lloyd,
    "cvt-block-diagonal": cvt.block_diagonal,
    "cvt-full": cvt.full,
    #
    "cpt-linear-solve": cpt.linear_solve,
    "cpt-fixed-point": cpt.fixed_point,
    "cpt-quasi-newton": cpt.quasi_newton,
    #
    "laplace": laplace,
    #
    "odt-fixed-point": odt.fixed_point,
}


def get_new_points(mesh, method: str):
    return methods[method.lower()].get_new_points(mesh)


def optimize(mesh, method: str, *args, **kwargs):
    # Normalize the method name, e.g.,
    #   ODT  (block diagonal) -> odt-block-diagonal
    normalized_method = "-".join(
        filter(lambda item: item != "", re.split("-| |\\(|\\)", method.lower()))
    )

    # Special treatment for ODT. We're using scipy.optimize there.
    if normalized_method[:3] == "odt" and normalized_method[4:] != "fixed-point":
        min_method = normalized_method[4:]
        if "omega" in kwargs:
            assert kwargs["omega"] == 1.0
            kwargs.pop("omega")
        odt.nonlinear_optimization(mesh, min_method, *args, **kwargs)
        return

    return _optimize(methods[normalized_method].get_new_points, mesh, *args, **kwargs)


def optimize_points_cells(X, cells, method: str, *args, **kwargs):
    mesh = meshplex.MeshTri(X, cells)
    optimize(mesh, method, *args, **kwargs)
    return mesh.points, mesh.cells["points"]


def _optimize(
    get_new_points,
    mesh,
    tol: float,
    max_num_steps,
    omega: float = 1.0,
    method_name=None,
    verbose=False,
    callback=None,
    step_filename_format=None,
    implicit_surface=None,
    implicit_surface_tol=1.0e-10,
    boundary_step=None,
):
    k = 0

    if verbose:
        print("\nBefore:")
        print_stats(mesh)
    if step_filename_format:
        mesh.save(
            step_filename_format.format(k),
            show_coedges=False,
            show_axes=False,
            cell_quality_coloring=("viridis", 0.0, 1.0, False),
        )

    if callback:
        callback(k, mesh)

    # mesh.write("out0.vtk")
    mesh.flip_until_delaunay()
    # mesh.write("out1.vtk")

    while True:
        k += 1

        new_points = get_new_points(mesh)

        # Move boundary points to the domain boundary, if given. If not just move the
        # points back to their original positions.
        idx = mesh.is_boundary_point
        if boundary_step is None:
            # Reset boundary points to their original positions.
            new_points[idx] = mesh.points[idx]
        else:
            # Move all boundary points back to the boundary.
            new_points[idx] = boundary_step(new_points[idx].T).T

        diff = omega * (new_points - mesh.points)

        # Some methods are stable (CPT), others can break down if the mesh isn't very
        # smooth. A break-down manifests, for example, in a step size that lets
        # triangles become completely flat or even "overshoot". After that, anything can
        # happen. To prevent this, restrict the maximum step size to half of the minimum
        # the incircle radius of all adjacent cells. This makes sure that triangles
        # cannot "flip".
        # <https://stackoverflow.com/a/57261082/353337>
        max_step = numpy.full(mesh.points.shape[0], numpy.inf)
        numpy.minimum.at(
            max_step,
            mesh.cells["points"].reshape(-1),
            numpy.repeat(mesh.cell_inradius, 3),
        )
        max_step *= 0.5
        #
        step_lengths = numpy.sqrt(numpy.einsum("ij,ij->i", diff, diff))
        # alpha = numpy.min(max_step / step_lengths)
        # alpha = numpy.min([alpha, 1.0])
        # diff *= alpha
        idx = step_lengths > max_step
        diff[idx] *= max_step[idx, None] / step_lengths[idx, None]

        new_points = mesh.points + diff

        # project all points back to the surface, if any
        if implicit_surface is not None:
            fval = implicit_surface.f(new_points.T)
            while numpy.any(numpy.abs(fval) > implicit_surface_tol):
                grad = implicit_surface.grad(new_points.T)
                grad_dot_grad = numpy.einsum("ij,ij->j", grad, grad)
                # The step is chosen in the direction of the gradient with a step size
                # such that, if the function was linear, the boundary (fval=0) would be
                # hit in one step.
                new_points -= (grad * (fval / grad_dot_grad)).T
                # compute new value
                fval = implicit_surface.f(new_points.T)

        mesh.points = new_points
        mesh.flip_until_delaunay()
        # mesh.show(control_volume_centroid_color="C1")
        # mesh.show()

        # Abort the loop if the update was small
        diff_norm_2 = numpy.einsum("ij,ij->i", diff, diff)
        is_final = numpy.all(diff_norm_2 < tol ** 2) or k >= max_num_steps

        if is_final or step_filename_format:
            if is_final:
                info = f"{k} steps"
                if method_name is not None:
                    if abs(omega - 1.0) > 1.0e-10:
                        method_name += f", relaxation parameter {omega}"
                    info += " of " + method_name

                if verbose:
                    print(f"\nFinal ({info}):")
                    print_stats(mesh)
            if step_filename_format:
                mesh.save(
                    step_filename_format.format(k),
                    show_coedges=False,
                    show_axes=False,
                    cell_quality_coloring=("viridis", 0.0, 1.0, False),
                )
        if callback:
            callback(k, mesh)

        if is_final:
            break

    return k, numpy.max(numpy.sqrt(diff_norm_2))
