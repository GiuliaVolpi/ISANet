""" Optimizer Module.
"""
import numpy as np
import time
import copy
import isanet.metrics as metrics
from isanet.optimizer import Optimizer
from isanet.optimizer.linesearch import line_search_wolfe, line_search_wolfe_f, phi_function
from isanet.optimizer.utils import make_vector, restore_w_to_model

class LBFGS(Optimizer):
    """Limited-memory LBFGS (L-BFGS)
    
    Parameters
    ----------
    m : integer, default=3
        The Hessian approximation will keep the curvature information from the 'm' 
        most recent iterations.

    c1 : float, default=1e-4
        Parameter for the Armijo-Wolfe line search.

    c2 : float, default=0.9
        Parameter for the Armijo-Wolfe line search.

    ln_maxiter : integer, default=10
        Maximum number of iterations of the Line Search.

    tol : float, optional
        Tolerance for the optimization. When the loss on training is
        not improving by at least tol for 'n_iter_no_change' consecutive 
        iterations convergence is considered to be reached and training stops.

    n_iter_no_change : integer, optional
        Maximum number of iterations with no improvements > tol.

    norm_g_eps : float, optional      
        Threshold that is used to decide whether to stop the 
        fitting of the model (it stops if the norm of the gradient reaches 
        'norm_g_eps').

    l_eps : float, optional       
        Threshold that is used to decide whether to stop the 
        fitting of the model (it stops if the loss function reaches 
        'l_eps'). 

    Methods
    -------

    optimize(self, model, epochs, X_train, Y_train, validation_data, batch_size, es, verbose)

    backpropagation(self, model, weights, X, Y)
       
    step(self, model, X, Y, verbose)
        L-BFGS algorithm.

    compute_search_dir(self, g, H0, s, y)
        Computes the search direction.

    append_history(self, alpha, norm_g, ls_log)
        Adds results to the history.

    """
    def __init__(self, m = 3, c1=1e-4, c2=.9, ln_maxiter = 10, tol = None, 
                 n_iter_no_change = None, norm_g_eps = None, l_eps = None, 
                 debug = False):
        super().__init__(tol = tol, n_iter_no_change = n_iter_no_change, norm_g_eps = norm_g_eps, l_eps = l_eps, debug = debug)
        self.c1 = c1
        self.c2 = c2
        self.old_phi0 = None
        self.past_g = 0
        self.past_d = 0
        self.past_ng = 0
        self.w = 0
        self.restart = 0
        self.s = []
        self.y = []
        self.m = m
        self.ln_maxiter = ln_maxiter

        self.history = {"alpha":        [],
                        "norm_g":       [],
                        "ls_conv":      [],
                        "ls_it":        [],
                        "ls_time":      [],
                        "zoom_used":    [],
                        "zoom_conv":    [],
                        "zoom_it":      []} 

    def backpropagation(self, model, weights, X, Y):
        g = super().backpropagation(model, weights, X, Y)
        for i in range(len(g)):
            g[i]  = (2/X.shape[0])*g[i] + (2*model.kernel_regularizer[0])*weights[i]
        return g

    def step(self, model, X, Y, verbose):
        current_batch_size = X.shape[0]

        w0 = make_vector(model.weights)
        g = make_vector(self.backpropagation(model, model.weights, X, Y))
        norm_g = np.linalg.norm(g)
        phi0 = metrics.mse_reg(Y, model.predict(X), model, model.weights)

        if ~model.is_fitted and self.epoch == 0:
            d = - g
        else:
            self.y[-1] = g - self.y[-1]
            gamma = np.dot(self.s[-1].T, self.y[-1])/np.dot(self.y[-1].T, self.y[-1])
            H0 = gamma
            d = -self.compute_search_dir(g, H0, self.s, self.y)
            curvature_condition = np.dot(self.s[-1].T, self.y[-1])
            if curvature_condition <= 1e-8:
                print("curvature condition: {}".format(curvature_condition))
                raise Exception("Curvature condition is negative")

        phi = phi_function(model, self, w0, X, Y, d)
        ls_verbose = False
        if verbose >=3:
            ls_verbose = True
        alpha, ls_log = line_search_wolfe(phi = phi.phi, derphi= phi.derphi, 
                                          phi0 = phi0, old_phi0 = self.old_phi0, 
                                          c1=self.c1, c2=self.c2, verbose = ls_verbose)

        self.old_phi0 = phi0
        w1 = w0 + alpha*d
        l_w1 = restore_w_to_model(model, w1)
        for i in range(0, len(model.weights)):
            regularizer = model.kernel_regularizer[i]*current_batch_size/self.tot_n_patterns
            weights_decay = 2*regularizer*model.weights[i]
            # weights_decay[0,:] = 0 # In ML the bias should not be regularized
            model.weights[i] = l_w1[i] - weights_decay
        
        if( len(self.s) == self.m and len(self.y) == self.m):
            self.s.pop(0)
            self.y.pop(0)
        self.s.append(w1 - w0)
        self.y.append(g)
        if verbose >= 2:
            print("| alpha: {} | ng: {} | ls conv: {}, it: {}, time: {:4.4f} | zoom used: {}, conv: {}, it: {}|".format(
                    alpha, norm_g, ls_log["ls_conv"], ls_log["ls_it"], ls_log["ls_time"],
                    ls_log["zoom_used"], ls_log["zoom_conv"], ls_log["zoom_it"])) 
        self.append_history(alpha, norm_g, ls_log)
        return norm_g


    def compute_search_dir(self, g, H0, s, y):
        q = copy.deepcopy(g)
        a = []
        for s_i, y_i in zip(reversed(s), reversed(y)):
            p = 1/(np.dot(y_i.T, s_i))
            alpha = p*np.dot(s_i.T,q)
            a.append(alpha)
            q -= alpha*y_i
    
        r = H0*q
        for s_i, y_i, a_i in zip(s, y, reversed(a)):
            p = 1/(np.dot(y_i.T, s_i))
            b = p*np.dot(y_i.T,r)
            r += s_i*(a_i -b)
        return r


    def append_history(self, alpha, norm_g, ls_log):
        self.history["alpha"].append(alpha)
        self.history["norm_g"].append(norm_g)
        self.history["ls_conv"].append(ls_log["ls_conv"])
        self.history["ls_it"].append(ls_log["ls_it"])
        self.history["ls_time"].append(ls_log["ls_time"])
        self.history["zoom_used"].append(ls_log["zoom_used"])
        self.history["zoom_conv"].append(ls_log["zoom_conv"])
        self.history["zoom_it"].append(ls_log["zoom_it"])